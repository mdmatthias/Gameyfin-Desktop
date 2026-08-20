"""Service layer for Gameyfin.

Service classes are re-exported lazily (like ``gameyfin_frontend`` itself)
so that importing a single submodule — e.g. ``services.update_service``
from ``dialogs`` — does not pull in every service. Several services import
``dialogs`` at module level, so eager re-exports would create a circular
import depending on which module is loaded first.
"""

__all__ = [
    "LauncherResolver",
    "GameInstaller",
    "GameLauncher",
    "PrefixService",
    "DownloadHistoryService",
    "ShortcutService",
    "MigrationService",
    "SteamIntegrationService",
    "GameyfinApiClient",
    "ImageCache",
    "WebViewRpc",
]


def __getattr__(name):
    if name == "LauncherResolver":
        from .launcher_resolver import LauncherResolver
        return LauncherResolver
    if name == "GameInstaller":
        from .game_installer import GameInstaller
        return GameInstaller
    if name == "GameLauncher":
        from .game_launcher import GameLauncher
        return GameLauncher
    if name == "PrefixService":
        from .prefix_service import PrefixService
        return PrefixService
    if name == "DownloadHistoryService":
        from .download_history_service import DownloadHistoryService
        return DownloadHistoryService
    if name == "ShortcutService":
        from .shortcut_service import ShortcutService
        return ShortcutService
    if name == "MigrationService":
        from .migration_service import MigrationService
        return MigrationService
    if name == "SteamIntegrationService":
        from .steam_integration import SteamIntegrationService
        return SteamIntegrationService
    if name == "GameyfinApiClient":
        from .gameyfin_api import GameyfinApiClient
        return GameyfinApiClient
    if name == "ImageCache":
        from .image_cache import ImageCache
        return ImageCache
    if name == "WebViewRpc":
        from .webview_rpc import WebViewRpc
        return WebViewRpc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
