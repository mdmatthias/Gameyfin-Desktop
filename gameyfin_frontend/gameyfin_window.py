import os
from os import getenv
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QTabWidget
from PyQt6.QtGui import QCloseEvent, QIcon, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from .download_manager import DownloadManagerWidget

class UrlCatchingPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, _type, _is_main_frame):
        QDesktopServices.openUrl(url)
        self.deleteLater()
        return False

class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, base_url, profile, parent=None):
        super().__init__(profile, parent)
        self.base_url = base_url
        self.allowed_hosts = {self.base_url.host()}
        sso_provider_host = getenv("GF_SSO_PROVIDER_HOST", None)
        if sso_provider_host:
            # Parse only the host, https://sso.host.com -> sso.host.com
            sso_host = QUrl(sso_provider_host).host() or sso_provider_host
            if sso_host:
                self.allowed_hosts.add(sso_host)

    def createWindow(self, _type):
        return UrlCatchingPage(self)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame:
            requested_host = url.host()
            if requested_host and requested_host not in self.allowed_hosts:
                QDesktopServices.openUrl(url)
                return False

        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

class GameyfinWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gameyfin")
        self.setGeometry(0, 0, int(getenv("GF_WINDOW_WIDTH", 1420)), int(getenv("GF_WINDOW_HEIGHT", 940)))
        self.is_really_quitting = False
        script_dir = os.path.dirname(os.path.abspath(__file__))

        icon_path = os.path.join(script_dir, "icon.png")

        profile_path = os.path.join(script_dir, ".gameyfin-app-data")
        os.makedirs(profile_path, exist_ok=True)

        # --- Profile setup ---
        self.profile = QWebEngineProfile("gameyfin-profile", self)
        self.profile.setPersistentStoragePath(profile_path)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        settings = self.profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)

        # --- Browser Setup ---
        self.browser = QWebEngineView()
        base_url = QUrl(getenv("GF_URL", "http://localhost:8080"))
        self.custom_page = CustomWebEnginePage(base_url, self.profile, self.browser)
        self.browser.setPage(self.custom_page)
        self.browser.setUrl(base_url)

        # --- Download Manager Setup ---
        self.download_manager = DownloadManagerWidget(profile_path, self)

        # --- Tab Widget Setup ---
        self.tab_widget = QTabWidget()

        # Add the Gameyfin tab with an empty string for the label
        gameyfin_tab_index = self.tab_widget.addTab(self.browser, "")
        # Set the icon for that tab
        self.tab_widget.setTabIcon(gameyfin_tab_index, QIcon(icon_path))

        # Add the Downloads tab
        self.tab_widget.addTab(self.download_manager, "Downloads")

        self.setCentralWidget(self.tab_widget)

        # --- Connect Signals ---
        self.browser.page().profile().downloadRequested.connect(self.on_download_requested)

        # --- Scrollbar Script ---
        script = QWebEngineScript()
        script.setSourceCode("""
            document.documentElement.style.overflowX = 'hidden';
            document.body.style.overflowX = 'hidden';
        """)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        self.browser.page().scripts().insert(script)

    def show_downloads_tab(self):
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.download_manager)

    @staticmethod
    def parse_size(text: str) -> int:
        try:
            num, unit = text.split()
            num = float(num.replace(",", "."))
            multipliers = {
                "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
                "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4
            }
            return int(num * multipliers.get(unit, 1))
        except Exception:
            return 0

    def closeEvent(self, event: QCloseEvent):
        if self.is_really_quitting:
            # This is a real quit, run cleanup
            self.download_manager.close()
            self.browser.setPage(None)
            self.browser.deleteLater()
            event.accept() # Accept the event and close
        else:
            # This is just the 'X' button, so hide
            event.ignore()
            self.hide()

    def on_download_requested(self, download: QWebEngineDownloadRequest):
        suggested_path = os.path.join(
            os.path.expanduser("~/Downloads"),
            os.path.basename(download.downloadFileName())
        )

        path, _ = QFileDialog.getSaveFileName(self, "Save File As", suggested_path)

        if not path:
            download.cancel()
            return

        download.setDownloadFileName(path)

        # --- Extract the download size from the download button text ---
        def handle_js_result(result):
            total_size = self.parse_size(result)
            download.accept()
            self.download_manager.add_download(download, total_size)
            self.tab_widget.setCurrentWidget(self.download_manager)

        js = """(function() {
            let el = document.querySelector('button .text-xs');
            return el ? el.innerText : "";
        })();"""

        self.browser.page().runJavaScript(js, 0, handle_js_result)