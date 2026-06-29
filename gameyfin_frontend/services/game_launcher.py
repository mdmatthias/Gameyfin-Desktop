"""Game launching — platform-specific execution via QProcess."""

from __future__ import annotations

import logging
import os
from typing import Any

from PyQt6.QtCore import QProcess

from gameyfin_frontend.utils import build_umu_env_prefix
from gameyfin_frontend.config import DEFAULT_PROTON

logger = logging.getLogger(__name__)


class GameLauncher:
    """Launches games via QProcess — Windows direct exec, Linux via UMU."""

    def start_windows(self, launcher_to_run: str) -> QProcess | None:
        """Launch a game executable directly via QProcess (Windows path).

        Args:
            launcher_to_run: Absolute path to the .exe to launch.

        Returns:
            The QProcess instance, or ``None`` if launch failed.
        """
        try:
            logger.info("Executing (Windows): %s", launcher_to_run)
            process = QProcess()
            process.setProgram(launcher_to_run)
            process.setWorkingDirectory(os.path.dirname(launcher_to_run))
            process.start()
            if not process.waitForStarted():
                logger.info("Launch failed (QProcess failed to start).")
                return None

            return process
        except OSError as e:
            logger.error("Launch failed: %s", e)
            return None

    def start_linux(
        self,
        launcher_to_run: str,
        target_dir: str,
        install_config: dict[str, Any],
        wine_prefix_path: str,
        proton_path: str = DEFAULT_PROTON,
    ) -> QProcess | None:
        """Launch a game via UMU environment prefix and umu-run on Linux.

        Builds the command string with ``build_umu_env_prefix`` and executes it
        via ``/bin/sh -c`` with ``exec`` for proper signal forwarding.

        Args:
            launcher_to_run: Path to the game executable.
            target_dir: Download target directory (unused, for future use).
            install_config: Dict of environment variables and UMU settings.
            wine_prefix_path: Path to the Wine prefix directory.
            proton_path: Proton version string. Defaults to "GE-Proton".

        Returns:
            The QProcess instance, or ``None`` if launch failed.
        """
        try:
            config = install_config or {}

            if not wine_prefix_path:
                raise ValueError("Wineprefix path was not set.")

            launcher_dir = os.path.dirname(launcher_to_run)

            logger.info("[Install] Applying user environment configuration:")
            for key, value in config.items():
                logger.info("  %s=%s", key, value)

            env_prefix = build_umu_env_prefix(proton_path, wine_prefix_path, config)

            logger.info("Executing: /bin/sh -c \"%s\"", f"{env_prefix} exec umu-run \"{launcher_to_run}\"")
            process = QProcess()
            process.setWorkingDirectory(launcher_dir)
            process.start("/bin/sh", ["-c", f"{env_prefix} exec umu-run \"{launcher_to_run}\""])

            if not process.waitForStarted():
                logger.info("Launch failed (QProcess failed to start).")
                return None

            return process
        except (ValueError, OSError) as e:
            logger.error("Launch failed: %s", e)
            return None
