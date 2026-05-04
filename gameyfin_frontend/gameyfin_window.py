import logging
import os
import sys
from typing import Any

from PyQt6.QtWidgets import QMainWindow, QFileDialog, QTabWidget, QApplication, QTabBar
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QStandardPaths, pyqtSignal, Qt
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from qt_material import apply_stylesheet

from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.umu_database import UmuDatabase

from .settings_widget import SettingsWidget
from .settings import settings_manager
from .utils import get_effective_icon

logger = logging.getLogger(__name__)


class CustomWebEnginePage(QWebEnginePage):
    # Signal to request a new tab with a specific URL
    new_tab_requested = pyqtSignal(QUrl)
    # Signal to request redirecting back to main tab
    main_tab_redirect_requested = pyqtSignal(QUrl)
    # Signal when logout is detected
    logout_detected = pyqtSignal(QUrl)

    def __init__(self, profile: Any, parent: QWebEnginePage | None = None, restricted_host: str | None = None, main_host: str | None = None):
        super().__init__(profile, parent)
        self.restricted_host = restricted_host
        self.main_host = main_host
        self.create_window_callback = None

    def set_restricted_host(self, host: str) -> None:
        self.restricted_host = host
        self.main_host = host

    def set_main_host(self, host: str) -> None:
        self.main_host = host

    def createWindow(self, _type):
        if self.create_window_callback:
            return self.create_window_callback(_type)
        return None

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame:
            # 1. Detect Logout
            if self.main_host and url.host() == self.main_host and '/logout' in url.path():
                 self.logout_detected.emit(url)
                 # If we are the main page (restricted_host is set), allow the navigation to proceed
                 if self.restricted_host:
                     return True
                 # If external tab, block it and let the signal handler redirect the main tab
                 return False

            # 2. Standard Host Restrictions
            if self.restricted_host:
                # If we have a restricted host, ensure we stay on it
                if url.host() and url.host() != self.restricted_host:
                    # Only open in new tab for link clicks and typed URLs.
                    # We allow FormSubmitted and Other (redirects) to stay in the main tab
                    # to prevent breaking authentication flows and avoiding "Form submission did not navigate away" errors.
                    if nav_type in (QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
                                    QWebEnginePage.NavigationType.NavigationTypeTyped):
                        self.new_tab_requested.emit(url)
                        return False
            elif self.main_host:
                # If we are in an external tab but navigate to the main host
                if url.host() and url.host() == self.main_host:
                    self.main_tab_redirect_requested.emit(url)
                    return False

        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class GameyfinWindow(QMainWindow):
    def __init__(self, umu_database: UmuDatabase) -> None:
        super().__init__()
        self.umu_database = umu_database
        self.setWindowTitle("Gameyfin")
        self.setGeometry(0, 0, settings_manager.get("GF_WINDOW_WIDTH"), settings_manager.get("GF_WINDOW_HEIGHT"))
        self.is_really_quitting = False
        
        profile_path = settings_manager.get_config_dir()
        os.makedirs(profile_path, exist_ok=True)

        self.profile = QWebEngineProfile("gameyfin-profile", self)
        self.profile.setPersistentStoragePath(profile_path)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        settings = self.profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)

        self._cookies = {}
        cookie_store = self.profile.cookieStore()
        cookie_store.cookieAdded.connect(self._on_cookie_added)
        cookie_store.cookieRemoved.connect(self._on_cookie_removed)
        cookie_store.loadAllCookies()

        self.browser = QWebEngineView()
        base_url = QUrl(settings_manager.get("GF_URL"))
        
        # Main page restricted to the Gameyfin host
        self.custom_page = CustomWebEnginePage(self.profile, self.browser, restricted_host=base_url.host(), main_host=base_url.host())
        self.custom_page.new_tab_requested.connect(self.add_new_browser_tab)
        self.custom_page.logout_detected.connect(self.handle_logout)
        self.custom_page.create_window_callback = self.create_new_window_for_page
        
        self.browser.setPage(self.custom_page)
        self.browser.setUrl(base_url)

        self.download_manager = DownloadManagerWidget(umu_database, self)
        self.prefix_manager = PrefixManagerWidget(umu_database, self)
        self.download_manager.prefix_manager = self.prefix_manager

        # --- Settings Setup ---
        self.settings_widget = SettingsWidget(self)

        # --- Tab Widget Setup ---
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)

        # Add the Gameyfin tab with an empty string for the label
        gameyfin_tab_index = self.tab_widget.addTab(self.browser, "")
        
        # Remove close button from the main tab (index 0)
        self.tab_widget.tabBar().setTabButton(gameyfin_tab_index, QTabBar.ButtonPosition.RightSide, None)
        
        # Set the icon for that tab
        tab_icon = get_effective_icon(
            custom_path=settings_manager.get("GF_ICON_PATH"),
            theme=settings_manager.get("GF_THEME")
        )

        self.tab_widget.setTabIcon(gameyfin_tab_index, tab_icon)

        downloads_index = self.tab_widget.addTab(self.download_manager, "Downloads")
        self.tab_widget.tabBar().setTabButton(downloads_index, QTabBar.ButtonPosition.RightSide, None)

        prefixes_index = self.tab_widget.addTab(self.prefix_manager, "Prefixes")
        self.tab_widget.tabBar().setTabButton(prefixes_index, QTabBar.ButtonPosition.RightSide, None)

        settings_index = self.tab_widget.addTab(self.settings_widget, "Settings")
        self.tab_widget.tabBar().setTabButton(settings_index, QTabBar.ButtonPosition.RightSide, None)

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

    def close_tab(self, index: int) -> None:
        """Close an external browser tab, preventing closure of the four fixed tabs."""
        # Prevent closing the fixed tabs (Main, Downloads, Prefixes, Settings)
        if index < 4:
            return
        
        widget = self.tab_widget.widget(index)
        if widget:
            widget.deleteLater()
            self.tab_widget.removeTab(index)

    def _setup_new_view(self) -> QWebEngineView:
        """Create a new browser view with a CustomWebEnginePage and connect tab signals."""
        view = QWebEngineView()
        base_url = QUrl(settings_manager.get("GF_URL"))
        page = CustomWebEnginePage(self.profile, view, restricted_host=None, main_host=base_url.host())
        page.new_tab_requested.connect(self.add_new_browser_tab)
        page.main_tab_redirect_requested.connect(self.redirect_to_main_tab)
        page.logout_detected.connect(self.handle_logout)
        page.create_window_callback = self.create_new_window_for_page
        view.setPage(page)
        
        view.titleChanged.connect(lambda title: self.update_tab_title(view, title))
        view.iconChanged.connect(lambda icon: self.update_tab_icon(view, icon))
        return view

    def add_new_browser_tab(self, url: QUrl) -> QWebEnginePage | None:
        """Add a new browser tab for an external URL and switch to it."""
        view = self._setup_new_view()
        view.setUrl(url)
        index = self.tab_widget.addTab(view, url.host() or "External")
        self.tab_widget.setCurrentIndex(index)
        view.show()
        return view.page()

    def create_new_window_for_page(self, _type: Any) -> QWebEnginePage | None:
        """Create a new browser tab when the embedded page requests a new window."""
        view = self._setup_new_view()
        index = self.tab_widget.addTab(view, "Loading...")
        self.tab_widget.setCurrentIndex(index)
        return view.page()

    def handle_logout(self, url: QUrl) -> None:
        """Close all external tabs and navigate the main tab to the logout URL."""
        # Close all external tabs (starting from the end to avoid index shift issues)
        count = self.tab_widget.count()
        # Fixed tabs are 0 (Main), 1 (Downloads), 2 (Prefixes), 3 (Settings) - indices < 4
        for i in range(count - 1, 3, -1):
            self.close_tab(i)
        
        # Ensure we are on the main tab
        self.tab_widget.setCurrentIndex(0)
        
        # Only navigate if the signal didn't come from the main page itself
        if self.sender() != self.browser.page():
            self.browser.setUrl(url)

    def redirect_to_main_tab(self, url: QUrl) -> None:
        """Switch to the main browser tab and navigate to the given URL."""
        self.tab_widget.setCurrentIndex(0)
        self.browser.setUrl(url)
        # Close tabs after redirect to main tab
        sender_page = self.sender()
        if isinstance(sender_page, CustomWebEnginePage):
            count = self.tab_widget.count()
            for i in range(count - 1, 3, -1):
                self.close_tab(i)

    def update_tab_title(self, view: QWebEngineView, title: str) -> None:
        """Update the tab label to reflect the browser view's new title."""
        idx = self.tab_widget.indexOf(view)
        if idx != -1:
            self.tab_widget.setTabText(idx, title)

    def update_tab_icon(self, view: QWebEngineView, icon: Any) -> None:
        """Update the tab icon to reflect the browser view's new favicon."""
        idx = self.tab_widget.indexOf(view)
        if idx != -1:
            self.tab_widget.setTabIcon(idx, icon)

    def show_main_tab(self) -> None:
        """Show the window and switch to the main Gameyfin browser tab."""
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.browser)

    def show_downloads_tab(self) -> None:
        """Show the window and switch to the Downloads tab."""
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.download_manager)

    def show_settings_tab(self) -> None:
        """Show the window and switch to the Settings tab."""
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.settings_widget)

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
        except (ValueError, IndexError):
            return 0

    def closeEvent(self, event: QCloseEvent) -> None:
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

    def _on_cookie_added(self, cookie) -> None:
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        value = bytes(cookie.value()).decode('utf-8', errors='replace')
        self._cookies[name] = value

    def _on_cookie_removed(self, cookie) -> None:
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        self._cookies.pop(name, None)

    def on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        url = download.url().toString()
        filename = os.path.basename(download.downloadFileName())
        zip_basename = os.path.splitext(filename)[0]

        default_download_dir = settings_manager.get("GF_DEFAULT_DOWNLOAD_DIR")
        prompt_download = settings_manager.get("GF_PROMPT_DOWNLOAD_DIR")

        if default_download_dir and os.path.exists(default_download_dir):
            target_base = default_download_dir
        else:
            target_base = os.path.expanduser("~/Downloads")

        suggested_dir = os.path.join(target_base, zip_basename)

        if prompt_download:
            selected = QFileDialog.getExistingDirectory(
                self, "Select download location", target_base,
                options=QFileDialog.Option.DontUseNativeDialog
            )
            if not selected:
                download.cancel()
                return
            # Always create a game subfolder inside the selected directory
            # so removing it never deletes the parent folder.
            if os.path.basename(selected) == zip_basename:
                target_dir = selected
            else:
                target_dir = os.path.join(selected, zip_basename)
        else:
            target_dir = suggested_dir

        download.cancel()

        cookies = dict(self._cookies)

        def handle_js_result(result):
            total_size = self.parse_size(result)
            record = {
                "path": target_dir,
                "filename": filename,
                "url": url,
                "status": "Downloading",
                "total_bytes": total_size,
            }
            worker = StreamDownloadWorker(url, target_dir, cookies, estimated_total=total_size)
            self.download_manager.add_download(worker, record)
            self.tab_widget.setCurrentWidget(self.download_manager)

        js = """(function() {
            let el = document.querySelector('button .text-xs');
            return el ? el.innerText : "";
        })();"""
        self.browser.page().runJavaScript(js, 0, handle_js_result)

    def apply_settings(self) -> None:
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
            new_host = new_url.host()
            if self.browser.url() != new_url:
                logger.info("Applying new URL: %s", new_url.toString())
                self.browser.setUrl(new_url)
            
            # Update the main_host in all custom pages
            for i in range(self.tab_widget.count()):
                widget = self.tab_widget.widget(i)
                if isinstance(widget, QWebEngineView):
                    page = widget.page()
                    if isinstance(page, CustomWebEnginePage):
                        if page.restricted_host:
                            page.set_restricted_host(new_host)
                        else:
                            page.set_main_host(new_host)

        # 3. Update Icon
        app_icon = get_effective_icon(
            custom_path=settings_manager.get("GF_ICON_PATH"),
            theme=settings_manager.get("GF_THEME")
        )

        self.setWindowIcon(app_icon)
        # Update tab icon (index 0 is browser)
        self.tab_widget.setTabIcon(0, app_icon)

        # 4. Refresh UMU Database
        if sys.platform != "win32" and self.umu_database:
            self.umu_database.refresh_cache()

        # 5. Update Theme
        theme = settings_manager.get("GF_THEME")
        app = QApplication.instance()
        if theme and theme != "auto":
            apply_stylesheet(app, theme=theme)
        else:
            app.setStyleSheet("")
            if hasattr(app, 'default_palette'):
                app.setPalette(app.default_palette)
            if hasattr(app, 'default_font'):
                app.setFont(app.default_font)
            if hasattr(app, 'default_style_name'):
                app.setStyle(app.default_style_name)
