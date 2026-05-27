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
from gameyfin_frontend.utils import build_umu_env_prefix

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
                        return json.load(f), sd
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
        MangoHud usage.

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

        proton_path = config.get("PROTONPATH") or self.settings.get("PROTONPATH") or DEFAULT_PROTON
        env_part = build_umu_env_prefix(proton_path, prefix_path, config)

        count = 0
        for script_path in sh_files:
            try:
                logger.info("Checking script: %s", script_path)
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
                    parts = umu_run_line.split("umu-run")
                    if len(parts) > 1:
                        exe_args = parts[1].strip()
                        new_command = f"{env_part}umu-run {exe_args}"

                        # Determine working directory: prefer explicit cd line, fall back to exe parent dir
                        cd_line_match = re.search(r"cd '([^']+)'", content)
                        if cd_line_match:
                            working_dir = cd_line_match.group(1)
                        else:
                            # Extract exe path from umu-run line (e.g. umu-run "/path/to/dir/game.exe")
                            exe_match = re.search(r'umu-run\s+"([^"]+)"', umu_run_line)
                            if exe_match:
                                working_dir = os.path.dirname(exe_match.group(1))
                            else:
                                working_dir = None

                        if working_dir and os.path.isdir(working_dir):
                            new_content = f"#!/bin/sh\n\ncd '{working_dir}'\n\n# Auto-generated by Gameyfin\n{new_command}\n"
                        else:
                            new_content = f"#!/bin/sh\n\n# Auto-generated by Gameyfin\n{new_command}\n"

                        with open(script_path, 'w') as f:
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
