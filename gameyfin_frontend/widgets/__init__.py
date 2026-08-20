from .download_manager import DownloadManagerWidget
from .download_item import DownloadItemWidget
from .prefix_manager import PrefixManagerWidget, PrefixItemWidget
from .loading_overlay import LoadingOverlay
from .gamepad_hud import GamepadHelpOverlay, GamepadHintBar
from .library_browser import LibraryBrowserWidget
from .game_detail import GameDetailWidget

__all__ = [
    "DownloadManagerWidget",
    "DownloadItemWidget",
    "PrefixManagerWidget",
    "PrefixItemWidget",
    "LoadingOverlay",
    "GamepadHintBar",
    "GamepadHelpOverlay",
    "LibraryBrowserWidget",
    "GameDetailWidget",
]


def __getattr__(name):
    # Lazy imports handled in package-level __init__.py
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
