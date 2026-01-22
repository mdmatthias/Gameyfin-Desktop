import os
import sys
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QTabWidget, QApplication, QTabBar
from PyQt6.QtGui import QCloseEvent, QIcon, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QStandardPaths, pyqtSignal, Qt
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget

from .settings_widget import SettingsWidget
from .settings import settings_manager
from .utils import get_app_icon_path


class CustomWebEnginePage(QWebEnginePage):
    # Signal to request a new tab with a specific URL
    new_tab_requested = pyqtSignal(QUrl)
    # Signal to request redirecting back to main tab
    main_tab_redirect_requested = pyqtSignal(QUrl)
    # Signal when logout is detected
    logout_detected = pyqtSignal(QUrl)

    def __init__(self, profile, parent=None, restricted_host=None, main_host=None):
        super().__init__(profile, parent)
        self.restricted_host = restricted_host
        self.main_host = main_host
        self.create_window_callback = None

    def set_restricted_host(self, host):
        self.restricted_host = host
        self.main_host = host

    def set_main_host(self, host):
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
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)

        self.browser = QWebEngineView()
        base_url = QUrl(settings_manager.get("GF_URL"))
        
        # Main page restricted to the Gameyfin host
        self.custom_page = CustomWebEnginePage(self.profile, self.browser, restricted_host=base_url.host(), main_host=base_url.host())
        self.custom_page.new_tab_requested.connect(self.add_new_browser_tab)
        self.custom_page.logout_detected.connect(self.handle_logout)
        self.custom_page.create_window_callback = self.create_new_window_for_page
        
        self.browser.setPage(self.custom_page)
        self.browser.setUrl(base_url)

        self.download_manager = DownloadManagerWidget(profile_path, umu_database, self)

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
        is_light_variant = "icon_light.png" in icon_path
        has_custom_path = settings_manager.get("GF_ICON_PATH")
        
        if has_custom_path or is_light_variant:
            tab_icon = QIcon(icon_path)
        else:
            tab_icon = QIcon.fromTheme("org.gameyfin.Gameyfin-Desktop")
            if tab_icon.isNull():
                tab_icon = QIcon(icon_path)
                
        self.tab_widget.setTabIcon(gameyfin_tab_index, tab_icon)

        downloads_index = self.tab_widget.addTab(self.download_manager, "Downloads")
        self.tab_widget.tabBar().setTabButton(downloads_index, QTabBar.ButtonPosition.RightSide, None)

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

    def close_tab(self, index):
        # Prevent closing the fixed tabs (Main, Downloads, Settings)
        if index < 3:
            return
        
        widget = self.tab_widget.widget(index)
        if widget:
            widget.deleteLater()
            self.tab_widget.removeTab(index)

    def add_new_browser_tab(self, url):
        view = QWebEngineView()
        # External tabs are not restricted
        base_url = QUrl(settings_manager.get("GF_URL"))
        page = CustomWebEnginePage(self.profile, view, restricted_host=None, main_host=base_url.host())
        page.new_tab_requested.connect(self.add_new_browser_tab)
        page.main_tab_redirect_requested.connect(self.redirect_to_main_tab)
        page.logout_detected.connect(self.handle_logout)
        page.create_window_callback = self.create_new_window_for_page
        view.setPage(page)
        view.setUrl(url)
        
        index = self.tab_widget.addTab(view, url.host() or "External")
        self.tab_widget.setCurrentIndex(index)
        view.show()
        
        view.titleChanged.connect(lambda title: self.update_tab_title(view, title))
        view.iconChanged.connect(lambda icon: self.update_tab_icon(view, icon))
        return page

    def create_new_window_for_page(self, _type):
        view = QWebEngineView()
        base_url = QUrl(settings_manager.get("GF_URL"))
        page = CustomWebEnginePage(self.profile, view, restricted_host=None, main_host=base_url.host())
        page.new_tab_requested.connect(self.add_new_browser_tab)
        page.main_tab_redirect_requested.connect(self.redirect_to_main_tab)
        page.logout_detected.connect(self.handle_logout)
        page.create_window_callback = self.create_new_window_for_page
        view.setPage(page)
        
        index = self.tab_widget.addTab(view, "Loading...")
        self.tab_widget.setCurrentIndex(index)
        
        view.titleChanged.connect(lambda title: self.update_tab_title(view, title))
        view.iconChanged.connect(lambda icon: self.update_tab_icon(view, icon))

        return page

    def handle_logout(self, url):
        # Close all external tabs (starting from the end to avoid index shift issues)
        count = self.tab_widget.count()
        # Fixed tabs are 0 (Main), 1 (Downloads), 2 (Settings) - indices < 3
        for i in range(count - 1, 2, -1):
            self.close_tab(i)
        
        # Ensure we are on the main tab
        self.tab_widget.setCurrentIndex(0)
        
        # Only navigate if the signal didn't come from the main page itself
        if self.sender() != self.browser.page():
            self.browser.setUrl(url)

    def redirect_to_main_tab(self, url):
        self.tab_widget.setCurrentIndex(0)
        self.browser.setUrl(url)
        # Close tabs after redirect to main tab
        sender_page = self.sender()
        if isinstance(sender_page, CustomWebEnginePage):
            count = self.tab_widget.count()
            for i in range(count - 1, 2, -1):
                self.close_tab(i)

    def update_tab_title(self, view, title):
        idx = self.tab_widget.indexOf(view)
        if idx != -1:
            self.tab_widget.setTabText(idx, title)

    def update_tab_icon(self, view, icon):
        idx = self.tab_widget.indexOf(view)
        if idx != -1:
            self.tab_widget.setTabIcon(idx, icon)

    def show_main_tab(self):
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.browser)

    def show_downloads_tab(self):
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.download_manager)

    def show_settings_tab(self):
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
            new_host = new_url.host()
            if self.browser.url() != new_url:
                print(f"Applying new URL: {new_url.toString()}")
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
        # Logic matches main initialization
        custom_icon_path = settings_manager.get("GF_ICON_PATH")
        theme = settings_manager.get("GF_THEME")
        
        internal_icon_path = get_app_icon_path(custom_icon_path, theme=theme)
        
        is_light_variant = "icon_light.png" in internal_icon_path
        has_custom_path = custom_icon_path is not None and custom_icon_path != ""
        
        if has_custom_path or is_light_variant:
             app_icon = QIcon(internal_icon_path)
        else:
             app_icon = QIcon.fromTheme("org.gameyfin.Gameyfin-Desktop")
             if app_icon.isNull():
                 app_icon = QIcon(internal_icon_path)
             
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