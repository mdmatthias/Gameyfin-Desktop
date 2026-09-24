"""Manage Wine prefixes: scanning, config loading/saving, script updates, deletion."""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
from typing import Any

from gameyfin_frontend.config import DEFAULT_PROTON, SCRIPT_PERMISSION
from gameyfin_frontend.services.exe_icon import extract_exe_icon
from gameyfin_frontend.utils import build_umu_env_prefix, create_shortcuts, sanitize_name, shell_dquote

logger = logging.getLogger(__name__)


class PrefixService:
    """Handles prefix scanning, config management, script updates, and deletion."""

    def __init__(self, settings: Any) -> None:
        """Initialize the prefix service.

        Args:
            settings: SettingsManager instance providing app configuration.
        """
        self.settings = settings

    def get_all_prefixes(self) -> dict[str, str]:
        """Collect prefix directories from all configured prefix dirs (new + legacy).

        Returns:
            Dict mapping prefix name to full filesystem path. If the same name exists
            in multiple directories, the first (newest) location is preferred.
        """
        result: dict[str, str] = {}
        for prefix_base in self.settings.get_prefixes_dirs():
            if not os.path.exists(prefix_base):
                continue
            try:
                for item in os.listdir(prefix_base):
                    full_path = os.path.join(prefix_base, item)
                    if os.path.isdir(full_path) and item not in result:
                        result[item] = full_path
            except OSError as e:
                logger.error("Error reading prefix directory %s: %s", prefix_base, e)
        return result

    def load_config_from_scripts_dir(self, game_name: str) -> tuple[dict[str, Any], str | None]:
        """Load install config from a game's script directories.

        Checks both new and legacy locations for config.json. Falls back to parsing
        .sh scripts if no config.json is found.

        Handles three input formats:
        1. New per-script format: has ``ALL_SCRIPTS`` key with nested dict.
        2. Old per-script GAME_ARGS: flat fields + GAME_ARGS dict.
        3. Legacy flat format: all fields at top level.

        For formats 2 and 3, the config is expanded to per-script format so that
        every script gets a full copy of the flat values.

        Args:
            game_name: Name of the game (without _pfx suffix).

        Returns:
            Tuple of (config_dict, scripts_dir_path) or ({}, None) if nothing found.
        """
        scripts_dirs = self.settings.get_shortcuts_dirs(game_name)
        for sd in scripts_dirs:
            config_path = os.path.join(sd, "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                    config = self._migrate_config_format(config)
                    return config, sd
                except (json.JSONDecodeError, OSError) as e:
                    logger.error("Error loading config from %s: %s", config_path, e)

        # Fallback: try to parse from a .sh file
        for sd in scripts_dirs:
            if os.path.exists(sd):
                sh_files = glob.glob(os.path.join(sd, "*.sh"))
                if sh_files:
                    logger.info("Config not found, extracting from %s", sh_files[0])
                    config = self.extract_config_from_sh(sh_files[0])
                    return config, sd

        return {}, None

    def _migrate_config_format(self, config: dict[str, Any]) -> dict[str, Any]:
        """Migrate legacy config formats to the current per-script format.

        The current format uses ``ALL_SCRIPTS`` as a baseline with per-script
        overrides for fields that differ.  Legacy configs (flat keys, or flat
        keys with a GAME_ARGS dict) are expanded so that every script gets a
        full copy of the values.
        """
        # Already in new per-script format
        if "ALL_SCRIPTS" in config and isinstance(config["ALL_SCRIPTS"], dict):
            return config

        # Old per-script GAME_ARGS dict + flat fields
        raw_game_args = config.get("GAME_ARGS", "")
        if isinstance(raw_game_args, dict):
            baseline = {
                "PROTON_ENABLE_WAYLAND": config.get("PROTON_ENABLE_WAYLAND", "0"),
                "MANGOHUD": config.get("MANGOHUD", "0"),
                "PROTON_USE_WOW64": config.get("PROTON_USE_WOW64", "0"),
                "GAMEID": config.get("GAMEID", "umu-default"),
                "STORE": config.get("STORE", "none"),
                "PROTONPATH": config.get("PROTONPATH", ""),
                "GAME_ARGS": raw_game_args.get("ALL_SCRIPTS", ""),
                "EXTRA_VARS": self._extract_extra_vars_flat(config),
            }
            # Collect per-script overrides
            overrides: dict[str, dict[str, str]] = {}
            for key, value in raw_game_args.items():
                if key == "ALL_SCRIPTS":
                    continue
                overrides[key] = {"GAME_ARGS": value}
            # Build output
            output: dict[str, Any] = {"ALL_SCRIPTS": baseline}
            for script_name, override in overrides.items():
                output[script_name] = override
            return output

        # Legacy flat format: expand to per-script with all scripts getting
        # a full copy.  We don't know the script names here, so we return
        # the flat config as-is and let the caller (e.g. update_scripts)
        # handle it.
        return config

    @staticmethod
    def _extract_extra_vars_flat(config: dict[str, Any]) -> str:
        """Extract extra environment variables from a flat config dict."""
        extra_lines = []
        for k, v in config.items():
            if k not in ["PROTON_ENABLE_WAYLAND", "MANGOHUD", "GAMEID", "STORE",
                         "PROTON_USE_WOW64", "PROTONPATH", "GAME_ARGS"]:
                extra_lines.append(f"{k}={v}")
        return "\n".join(extra_lines)

    def save_config(self, game_name: str, config: dict[str, Any]) -> str:
        """Save install config to the primary (new) scripts directory.

        Args:
            game_name: Name of the game.
            config: Config dict to serialize.

        Returns:
            Path to the saved config.json file.
        """
        scripts_dir = self.settings.get_shortcuts_dir(game_name)
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir, exist_ok=True)

        config_path = os.path.join(scripts_dir, "config.json")
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
        logger.info("Saved config to %s", config_path)
        return config_path

    def extract_config_from_sh(self, script_path: str) -> dict[str, Any]:
        """Parse a .sh script to extract environment variables set before umu-run.

        Searches for the umu-run line, extracts ``KEY="VALUE"`` pairs, and detects
        MangoHud usage.  All fields are returned in the new per-script format
        with an ``ALL_SCRIPTS`` baseline.

        Args:
            script_path: Path to the .sh script file.

        Returns:
            Dict of extracted environment variable key-value pairs.
        """
        config: dict[str, Any] = {}
        try:
            with open(script_path, 'r') as f:
                content = f.read()

            lines = content.splitlines()
            umu_run_line = ""

            # Find the line with umu-run, searching backwards
            for line in reversed(lines):
                if "umu-run" in line:
                    umu_run_line = line
                    break

            if umu_run_line:
                # Split at umu-run to get the env var part
                env_part = umu_run_line.split("umu-run")[0]

                # Check if mangohud is used in front of umu-run
                if "mangohud" in env_part.lower():
                    config["MANGOHUD"] = "1"
                    env_part = env_part.replace("mangohud", "").strip()

                # Regex to find KEY="VALUE"
                matches = re.findall(r'(\w+)="(.*?)"', env_part)

                for key, value in matches:
                    if key not in ["WINEPREFIX"]:
                        config[key] = value

                # Extract game arguments from the part after umu-run
                after_umu = umu_run_line.split("umu-run", 1)[1].strip()
                # Match the exe path (quoted) and capture everything after it
                match = re.match(r'(?:"([^"]*)"' r"|'([^']*)'" r"|(\S+))(.*)", after_umu)
                if match:
                    game_args = match.group(4).strip()
                    if game_args:
                        config["GAME_ARGS"] = game_args

        except (OSError, IOError) as e:
            logger.error("Error extracting config from %s: %s", script_path, e)

        return config

    def update_scripts(
        self,
        prefix_path: str,
        config: dict[str, Any],
        game_name: str | None = None,
    ) -> int:
        """Update all .sh scripts for a game with new environment variables.

        Scans all script directories (new + legacy), rebuilds the umu-run
        command with the new env prefix, and preserves the original executable path.

        For per-script config (new format with ``ALL_SCRIPTS`` baseline), each
        script is updated with its own set of values merged from the baseline
        and any per-script overrides.

        Args:
            prefix_path: WINEPREFIX path.
            config: Dict of environment variables to write.
            game_name: Name of the game (for finding script dirs).
                Falls back to extracting from ``config`` or stripping ``_pfx``
                from ``prefix_path`` if not provided.

        Returns:
            Number of scripts updated.
        """
        # Resolve game name: explicit param > config key > derive from prefix_path
        if not game_name:
            game_name = config.get("GAME_NAME", "")
        if not game_name:
            pn = os.path.basename(prefix_path)
            if pn.endswith("_pfx"):
                game_name = pn[:-4]

        # Collect .sh files from all script dirs
        sh_files: list[str] = []
        for sd in self.settings.get_shortcuts_dirs(game_name):
            if os.path.exists(sd):
                sh_files.extend(glob.glob(os.path.join(sd, "*.sh")))

        if not sh_files:
            logger.info("No .sh scripts found to update.")
            return 0

        # Detect per-script format: has ALL_SCRIPTS key with nested dict
        has_per_script = "ALL_SCRIPTS" in config and isinstance(config["ALL_SCRIPTS"], dict)

        count = 0
        for script_path in sh_files:
            try:
                logger.info("Checking script: %s", script_path)
                with open(script_path, 'r', encoding="utf-8") as f:
                    content = f.read()

                lines = content.splitlines()
                umu_run_line = ""

                # Find the line with umu-run, searching backwards
                for line in reversed(lines):
                    if "umu-run" in line:
                        umu_run_line = line
                        break

                if umu_run_line:
                    parts = umu_run_line.split("umu-run")
                    if len(parts) > 1:
                        rest = parts[1].strip()
                        # Extract only the exe path (e.g. "/path/to/game.exe"),
                        # not any previously stored game args.
                        exe_match = re.match(r'(?:"([^"]*)"' r"|'([^']*)'" r"|(\S+))(.*)", rest)
                        if exe_match:
                            # Reconstruct just the quoted exe path
                            raw_exe = exe_match.group(1) or exe_match.group(2) or exe_match.group(3)
                            exe_path = re.sub(r'\\([\\"$`])', r'\1', raw_exe)
                            exe_args = shell_dquote(exe_path)
                        else:
                            exe_args = rest

                        # Determine per-script config values
                        script_name = os.path.basename(script_path)
                        if has_per_script:
                            # Merge ALL_SCRIPTS baseline with script-specific overrides
                            merged = dict(config["ALL_SCRIPTS"])
                            for key, value in config.items():
                                if key == "ALL_SCRIPTS":
                                    continue
                                if key == script_name and isinstance(value, dict):
                                    merged.update(value)
                            # Expand EXTRA_VARS into individual env vars
                            extra_text = merged.get("EXTRA_VARS", "")
                            if extra_text:
                                for line in extra_text.splitlines():
                                    if "=" in line:
                                        k, v = line.split("=", 1)
                                        merged[k.strip()] = v.strip()
                            # Extract and remove internal keys from env prefix
                            game_args = merged.pop("GAME_ARGS", "") or ""
                            merged.pop("EXTRA_VARS", None)
                            # Append GAME_ARGS to exe_args
                            if game_args:
                                exe_args = f"{exe_args} {game_args}"
                            proton_path = merged.get("PROTONPATH", "") or self.settings.get("PROTONPATH") or DEFAULT_PROTON
                            env_part = build_umu_env_prefix(proton_path, prefix_path, merged)
                        else:
                            # Legacy flat config
                            proton_path = config.get("PROTONPATH") or self.settings.get("PROTONPATH") or DEFAULT_PROTON
                            env_part = build_umu_env_prefix(proton_path, prefix_path, config)
                            game_args_config = config.get("GAME_ARGS", "")
                            if isinstance(game_args_config, dict):
                                game_args = game_args_config.get(script_name, "")
                                if not game_args:
                                    game_args = game_args_config.get("ALL_SCRIPTS", "")
                            else:
                                game_args = game_args_config if game_args_config else ""
                            if game_args:
                                exe_args = f"{exe_args} {game_args}"

                        new_command = f"{env_part}umu-run {exe_args}"

                        # Determine working directory: prefer explicit cd line, fall back to exe parent dir
                        # Older scripts single-quoted the cd target; newer ones double-quote it (with
                        # \\, \", \$, \` escaped) since the working dir may itself contain an apostrophe.
                        cd_line_match = re.search(r'cd "((?:\\.|[^"\\])*)"', content) or re.search(r"cd '([^']+)'", content)
                        if cd_line_match:
                            working_dir = re.sub(r'\\([\\"$`])', r'\1', cd_line_match.group(1))
                        else:
                            # Extract exe path from umu-run line (e.g. umu-run "/path/to/dir/game.exe")
                            exe_match = re.search(r'umu-run\s+"((?:\\.|[^"\\])*)"', umu_run_line)
                            if exe_match:
                                exe_path = re.sub(r'\\([\\"$`])', r'\1', exe_match.group(1))
                                working_dir = os.path.dirname(exe_path)
                            else:
                                working_dir = None

                        locale_export = "export LC_ALL=C.UTF-8\nexport LANG=C.UTF-8\n\n"
                        if working_dir and os.path.isdir(working_dir):
                            new_content = f"#!/bin/sh\n\n{locale_export}cd {shell_dquote(working_dir)}\n\n# Auto-generated by Gameyfin\n{new_command}\n"
                        else:
                            new_content = f"#!/bin/sh\n\n{locale_export}# Auto-generated by Gameyfin\n{new_command}\n"

                        with open(script_path, 'w', encoding="utf-8") as f:
                            f.write(new_content)

                        os.chmod(script_path, SCRIPT_PERMISSION)
                        count += 1
                        logger.info("Updated script: %s", script_path)
                    else:
                        logger.warning("Script %s has umu-run but parsing failed.", script_path)
                else:
                    logger.info("Script %s does not contain 'umu-run'.", script_path)

            except (OSError, IOError) as e:
                logger.error("Failed to update script %s: %s", script_path, e)

        return count

    def create_shortcut_for_exe(
        self,
        prefix_path: str,
        game_name: str,
        exe_path: str,
        shortcut_name: str,
        config: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Register an arbitrary executable as a shortcut of a prefix.

        Writes a .desktop file into the prefix's ``drive_c/proton_shortcuts``
        directory — the same place Proton drops the ones captured during
        install, so the shortcut manager lists it alongside them — and then
        generates its launcher .sh script the usual way, with the game's
        stored install config.

        Args:
            prefix_path: WINEPREFIX path.
            game_name: Name of the game (without ``_pfx``), used to find the scripts dir.
            exe_path: Full filesystem path to the Windows executable.
            shortcut_name: Display name for the shortcut.
            config: Install config to use. Loaded from the scripts dir when omitted.

        Returns:
            Tuple of (desktop_file_path, script_path).
        """
        if config is None:
            config, _ = self.load_config_from_scripts_dir(game_name)

        base = sanitize_name(os.path.splitext(shortcut_name)[0]).strip() or "script"

        shortcuts_dir = os.path.join(prefix_path, "drive_c", "proton_shortcuts")
        os.makedirs(shortcuts_dir, exist_ok=True)
        desktop_path = os.path.join(shortcuts_dir, f"{base}.desktop")

        working_dir = os.path.dirname(exe_path)
        entry = (
            "[Desktop Entry]\n"
            f"Name={base}\n"
            "Type=Application\n"
            "Categories=Application;Game;\n"
            f"Path={working_dir}\n"
            f"StartupWMClass={os.path.basename(exe_path)}\n"
            f"Exec={exe_path}\n"
        )

        # Icons live where Proton puts the ones it captures during install, so
        # copy_icon_from_source finds this one the same way.
        icon_path = os.path.join(shortcuts_dir, "icons", "256x256", "apps", f"{base}.png")
        if extract_exe_icon(exe_path, icon_path):
            entry += f"Icon={base}\n"
        with open(desktop_path, "w", encoding="utf-8") as f:
            f.write(entry)
        logger.info("Created proton shortcut %s for %s", desktop_path, exe_path)

        scripts_dir = self.settings.get_shortcuts_dir(game_name)
        proton_path = config.get("PROTONPATH") or self.settings.get("PROTONPATH") or DEFAULT_PROTON

        # Only this .desktop is passed in: the other shortcuts' scripts are left
        # exactly as they are, the same way Config → Update scripts leaves
        # shortcuts it wasn't asked about alone.
        create_shortcuts(
            all_desktop_files=[desktop_path],
            scripts_dir=scripts_dir,
            wine_prefix=prefix_path,
            install_config=config,
            proton_path=proton_path,
        )

        return desktop_path, os.path.join(scripts_dir, f"{base}.sh")

    def delete_prefix(self, prefix_path: str, game_name: str) -> None:
        """Delete a prefix directory and its associated shortcut scripts.

        Args:
            prefix_path: Full filesystem path to the Wine prefix.
            game_name: Name of the game (for finding associated scripts).
        """
        shutil.rmtree(prefix_path)

        # Also delete shortcut scripts from all configured locations
        for scripts_dir in self.settings.get_shortcuts_dirs(game_name):
            if os.path.exists(scripts_dir):
                shutil.rmtree(scripts_dir)
