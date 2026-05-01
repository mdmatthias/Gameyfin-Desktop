# Lazy imports to avoid pulling in QWebEngineView at package load time
# (which requires a running QCoreApplication and causes issues in tests)
def __getattr__(name):
    if name == "GameyfinTray":
        from .gameyfin_tray import GameyfinTray
        return GameyfinTray
    if name == "GameyfinWindow":
        from .gameyfin_window import GameyfinWindow
        return GameyfinWindow
    if name == "DownloadManagerWidget":
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        return DownloadManagerWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")