from .download_manager import DownloadManagerWidget
from .download_item import DownloadItemWidget
from .prefix_manager import PrefixManagerWidget, PrefixItemWidget
from .loading_overlay import LoadingOverlay

__all__ = [
    "DownloadManagerWidget",
    "DownloadItemWidget",
    "PrefixManagerWidget",
    "PrefixItemWidget",
    "LoadingOverlay",
]


def __getattr__(name):
    # Lazy imports handled in package-level __init__.py
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
