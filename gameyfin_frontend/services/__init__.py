"""Service layer for Gameyfin."""

from __future__ import annotations

from .launcher_resolver import LauncherResolver
from .game_installer import GameInstaller
from .game_launcher import GameLauncher
from .prefix_service import PrefixService
from .download_history_service import DownloadHistoryService
from .shortcut_service import ShortcutService
from .migration_service import MigrationService

__all__ = [
    "LauncherResolver",
    "GameInstaller",
    "GameLauncher",
    "PrefixService",
    "DownloadHistoryService",
    "ShortcutService",
    "MigrationService",
]
