import os
import sys
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QTabWidget
from PyQt6.QtGui import QCloseEvent, QIcon, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QStandardPaths
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget

from .settings_widget import SettingsWidget
from .settings import settings_manager

class UrlCatchingPage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)

    def acceptNavigationRequest(self, url, _type, _is_main_frame):
        QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, base_url, profile, parent=None):
        super().__init__(profile, parent)
        self.base_url = base_url
        self.allowed_hosts = {self.base_url.host()}
        sso_provider_host = settings_manager.get("GF_SSO_PROVIDER_HOST", "")
        if sso_provider_host:
            # Parse only the host, https://sso.host.com -> sso.host.com
            sso_host = QUrl(sso_provider_host).host() or sso_provider_host
            if sso_host:
                self.allowed_hosts.add(sso_host)

    def createWindow(self, _type):
        return UrlCatchingPage(self.profile(), self)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame:
            requested_host = url.host()
            if requested_host and requested_host not in self.allowed_hosts:
                QDesktopServices.openUrl(url)
                return False

        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class GameyfinWindow(QMainWindow):
    def __init__(self, umu_database):
        super().__init__()
        self.umu_database = umu_database
        self.setWindowTitle("Gameyfin")
        self.setGeometry(0, 0, settings_manager.get("GF_WINDOW_WIDTH"), settings_manager.get("GF_WINDOW_HEIGHT"))
        self.is_really_quitting = False
        script_dir = os.path.dirname(os.path.abspath(__file__))

        icon_path = os.path.join(script_dir, "icon.png")

        profile_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        os.makedirs(profile_path, exist_ok=True)

        self.profile = QWebEngineProfile("gameyfin-profile", self)
        self.profile.setPersistentStoragePath(profile_path)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        settings = self.profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)

        self.browser = QWebEngineView()
        base_url = QUrl(settings_manager.get("GF_URL"))
        self.custom_page = CustomWebEnginePage(base_url, self.profile, self.browser)
        self.browser.setPage(self.custom_page)
        self.browser.setUrl(base_url)

        self.download_manager = DownloadManagerWidget(profile_path, umu_database, self)

        # --- Settings Setup ---
        self.settings_widget = SettingsWidget(self)

        # --- Tab Widget Setup ---
        self.tab_widget = QTabWidget()

        # Add the Gameyfin tab with an empty string for the label
        gameyfin_tab_index = self.tab_widget.addTab(self.browser, "")
        # Set the icon for that tab
        tab_icon = QIcon.fromTheme("org.gameyfin.Gameyfin-Desktop")
        if tab_icon.isNull():
            tab_icon = QIcon(icon_path)
        self.tab_widget.setTabIcon(gameyfin_tab_index, tab_icon)

        self.tab_widget.addTab(self.download_manager, "Downloads")

        # Add the Settings tab
        self.tab_widget.addTab(self.settings_widget, "Settings")

        self.setCentralWidget(self.tab_widget)

        self.browser.page().profile().downloadRequested.connect(self.on_download_requested)

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
                "B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
                "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4
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
            event.accept()
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

    def apply_settings(self):
        """Applies settings dynamically without requiring a restart."""
        # 1. Update Window Geometry
        w = settings_manager.get("GF_WINDOW_WIDTH")
        h = settings_manager.get("GF_WINDOW_HEIGHT")
        if w and h:
            self.resize(w, h)

        # 2. Update Browser URL
        new_url_str = settings_manager.get("GF_URL")
        if new_url_str:
            new_url = QUrl(new_url_str)
            if self.browser.url() != new_url:
                print(f"Applying new URL: {new_url.toString()}")
                self.browser.setUrl(new_url)
                # Update the base_url in custom page for SSO logic
                if isinstance(self.browser.page(), CustomWebEnginePage):
                     self.browser.page().base_url = new_url
                     self.browser.page().allowed_hosts.add(new_url.host())

        # 3. Update SSO Host
        sso_provider_host = settings_manager.get("GF_SSO_PROVIDER_HOST", "")
        if sso_provider_host:
            sso_host = QUrl(sso_provider_host).host() or sso_provider_host
            if sso_host and isinstance(self.browser.page(), CustomWebEnginePage):
                print(f"Updating SSO host allowlist: {sso_host}")
                self.browser.page().allowed_hosts.add(sso_host)

        # 4. Update Icon
        icon_path = settings_manager.get("GF_ICON_PATH")
        # Logic matches main initialization
        app_icon = QIcon.fromTheme("org.gameyfin.Gameyfin-Desktop")
        
        if icon_path and os.path.exists(icon_path):
             app_icon = QIcon(icon_path)
        elif app_icon.isNull():
             # Fallback to default bundled icon
             script_dir = os.path.dirname(os.path.abspath(__file__))
             default_icon_path = os.path.join(script_dir, "icon.png")
             app_icon = QIcon(default_icon_path)
             
        self.setWindowIcon(app_icon)
        # Update tab icon (index 0 is browser)
        self.tab_widget.setTabIcon(0, app_icon)

        # 5. Refresh UMU Database
        if sys.platform != "win32" and self.umu_database:
            self.umu_database.refresh_cache()
             
        