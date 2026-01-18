import os
import sys
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QTabWidget, QApplication
from PyQt6.QtGui import QCloseEvent, QIcon, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QStandardPaths
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget

from .settings_widget import SettingsWidget
from .settings import settings_manager
from .utils import get_app_icon_path

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
            self.allowed_hosts.update(self.parse_sso_hosts(sso_provider_host))

    @staticmethod
    def parse_sso_hosts(sso_string):
        """
        Parses a comma-separated string of hosts.
        Ensures we extract the hostname correctly even if the user provides a port or no scheme.
        """
        hosts = set()
        if not sso_string:
            return hosts

        for part in sso_string.split(','):
            part = part.strip()
            if not part:
                continue
            
            # If no scheme is present, QUrl might not parse the host/port correctly.
            # Prepend https:// to ensure it's treated as a URL.
            if "://" not in part:
                part = f"https://{part}"
            
            qurl = QUrl(part)
            host = qurl.host()
            if host:
                hosts.add(host)
        return hosts

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
        
        icon_path = get_app_icon_path(settings_manager.get("GF_ICON_PATH"), theme=settings_manager.get("GF_THEME"))

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
        if sso_provider_host and isinstance(self.browser.page(), CustomWebEnginePage):
            hosts = CustomWebEnginePage.parse_sso_hosts(sso_provider_host)
            if hosts:
                print(f"Updating SSO host allowlist: {hosts}")
                self.browser.page().allowed_hosts.update(hosts)

        # 4. Update Icon
        # Logic matches main initialization
        app_icon = QIcon.fromTheme("org.gameyfin.Gameyfin-Desktop")
        
        custom_icon_path = settings_manager.get("GF_ICON_PATH")
        theme = settings_manager.get("GF_THEME")
        if custom_icon_path and os.path.exists(custom_icon_path):
             app_icon = QIcon(custom_icon_path)
        elif app_icon.isNull():
             # Fallback to theme-aware bundled icon
             app_icon = QIcon(get_app_icon_path(theme=theme))
             
        self.setWindowIcon(app_icon)
        # Update tab icon (index 0 is browser)
        self.tab_widget.setTabIcon(0, app_icon)

        # 5. Refresh UMU Database
        if sys.platform != "win32" and self.umu_database:
            self.umu_database.refresh_cache()

        # 6. Update Theme
        theme = settings_manager.get("GF_THEME")
        app = QApplication.instance()
        if theme and theme != "auto":
            from qt_material import apply_stylesheet
            apply_stylesheet(app, theme=theme)
        else:
            app.setStyleSheet("")
            if hasattr(app, 'default_palette'):
                app.setPalette(app.default_palette)
            if hasattr(app, 'default_font'):
                app.setFont(app.default_font)
            if hasattr(app, 'default_style_name'):
                app.setStyle(app.default_style_name)
        