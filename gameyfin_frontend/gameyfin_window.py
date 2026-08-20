import logging
import os
import sys
from typing import Any

from PyQt6.QtWidgets import (QMainWindow, QFileDialog, QTabWidget, QApplication, QTabBar,
                             QStackedWidget, QVBoxLayout, QWidget)
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QStandardPaths, QTimer, pyqtSignal, pyqtSlot, Qt
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from qt_material import apply_stylesheet

from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
from gameyfin_frontend.widgets.library_browser import LibraryBrowserWidget
from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
from gameyfin_frontend.widgets.loading_overlay import LoadingOverlay
from gameyfin_frontend.widgets.gamepad_hud import GamepadHintBar
from gameyfin_frontend.dialogs import UpdateDialog
from gameyfin_frontend.workers import StreamDownloadWorker, UpdateCheckWorker
from gameyfin_frontend.services.update_service import compare_versions, get_current_version
from gameyfin_frontend.services.gameyfin_api import GameyfinApiClient
from gameyfin_frontend.services.image_cache import ImageCache
from gameyfin_frontend.services.webview_rpc import WebViewRpc
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.utils import sanitize_name

from .gamepad import GamepadManager
from .gamepad_navigator import GamepadNavigator
from .gamepad_webnav import build_nav_script
from .settings_widget import SettingsWidget
from .settings import SettingsManager
from .utils import get_effective_icon, parse_size
from .config import (FIXED_TAB_COUNT, NATIVE_UI_COOKIE_DEBOUNCE_MS,
                     NATIVE_UI_PROBE_INTERVAL_MS)

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
    def __init__(self, umu_database: UmuDatabase, settings: SettingsManager) -> None:
        """Create the main application window with browser, downloads, prefixes, and settings tabs.

        Sets up the web profile, cookie store, custom page, and all child widgets.

        Args:
            umu_database: UmuDatabase instance for UMU game lookups.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__()
        self.umu_database = umu_database
        self.settings = settings
        self.setWindowTitle("Gameyfin")
        self.setGeometry(0, 0, settings.get("GF_WINDOW_WIDTH"), settings.get("GF_WINDOW_HEIGHT"))
        self.is_really_quitting = False

        self._setup_profile()
        self._setup_browser()
        self._setup_widgets()
        self._setup_tabs()
        self._inject_css()
        self._setup_gamepad()

    def _setup_profile(self) -> None:
        """Initialize the web browser profile with storage and settings."""
        profile_path = self.settings.get_config_dir()
        os.makedirs(profile_path, exist_ok=True)

        self.profile = QWebEngineProfile("gameyfin-profile", self)
        self.profile.setPersistentStoragePath(profile_path)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        web_settings = self.profile.settings()
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)

        self._cookies = {}
        cookie_store = self.profile.cookieStore()
        cookie_store.cookieAdded.connect(self._on_cookie_added)
        cookie_store.cookieRemoved.connect(self._on_cookie_removed)
        cookie_store.loadAllCookies()

    def _setup_browser(self) -> None:
        """Initialize the main browser view and custom page."""
        self.browser = QWebEngineView()
        base_url = QUrl(self.settings.get("GF_URL"))

        # Main page restricted to the Gameyfin host
        self.custom_page = CustomWebEnginePage(self.profile, self.browser, restricted_host=base_url.host(), main_host=base_url.host())
        self.custom_page.new_tab_requested.connect(self.add_new_browser_tab)
        self.custom_page.logout_detected.connect(self.handle_logout)
        self.custom_page.create_window_callback = self.create_new_window_for_page

        self.browser.setPage(self.custom_page)
        self.browser.setUrl(base_url)

    def _setup_widgets(self) -> None:
        """Initialize child widgets (Downloads, Prefixes, Settings)."""
        self.download_manager = DownloadManagerWidget(self.umu_database, self, self.settings)
        self.prefix_manager = PrefixManagerWidget(self.umu_database, self, self.settings)
        self.download_manager.prefix_manager = self.prefix_manager

        # --- Settings Setup ---
        self.settings_widget = SettingsWidget(self, self.settings)

        # --- Native library UI (feature-flagged) ---
        self.api_client: GameyfinApiClient | None = None
        self.image_cache: ImageCache | None = None
        self.library_browser: LibraryBrowserWidget | None = None
        if self._native_ui_requested():
            self._build_native_ui()

    def _current_page(self) -> QWebEnginePage | None:
        """Return the main web view's page, or None while it is being torn down."""
        browser = getattr(self, "browser", None)
        if browser is None:
            return None
        try:
            return browser.page()
        except RuntimeError:
            return None

    def _native_ui_requested(self) -> bool:
        """Return True when the native library UI is enabled in settings."""
        value = self.settings.get("GF_NATIVE_UI") or 0
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _build_native_ui(self) -> None:
        """Construct the API client, artwork cache and library browser once.

        The web view stays alive next to the browser in the main stack because
        login (including SSO) still happens there; it is shown only while the
        API reports us as unauthenticated.
        """
        if self.library_browser is not None:
            return

        # RPC calls run inside the logged-in page: the browser then attaches the
        # exact cookies and CSRF token the working web app uses, which a mirrored
        # cookie jar cannot always reproduce (scoped session cookies, or a token
        # that only exists in the document).
        self.webview_rpc = WebViewRpc(self._current_page, parent=self)
        self.api_client = GameyfinApiClient(
            self.settings,
            cookie_provider=lambda: dict(self._cookies),
            rpc_transport=self.webview_rpc,
        )
        self.image_cache = ImageCache(self.api_client, self.settings, self)
        self.library_browser = LibraryBrowserWidget(
            self.api_client, self.image_cache, self.settings, self
        )
        self.library_browser.download_requested.connect(self._on_native_download_requested)
        self.library_browser.login_required.connect(self._on_native_login_required)
        self.library_browser.library_loaded.connect(self._on_native_library_loaded)

        # Gameyfin completes login through client-side routing, so waiting for a
        # page load is not enough: poll until the API answers as authenticated.
        self._native_probe_timer = QTimer(self)
        self._native_probe_timer.setInterval(NATIVE_UI_PROBE_INTERVAL_MS)
        self._native_probe_timer.timeout.connect(self._probe_native_ui)
        # New cookies (i.e. a finished login) trigger a probe straight away
        self._native_cookie_timer = QTimer(self)
        self._native_cookie_timer.setSingleShot(True)
        self._native_cookie_timer.setInterval(NATIVE_UI_COOKIE_DEBOUNCE_MS)
        self._native_cookie_timer.timeout.connect(self._probe_native_ui)
        self.browser.urlChanged.connect(lambda _: self._probe_native_ui())

    def _setup_tabs(self) -> None:
        """Initialize the tab widget and add all tabs."""
        # --- Tab Widget Setup ---
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        # The active tab is already shown by its own selected styling; a
        # gamepad-driven focus ring drawn around the whole tab bar on top of
        # that is redundant, and LB/RB already switch tabs directly, so the
        # tab bar itself never needs to be a focus target.
        self.tab_widget.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Tab 0 holds the web view and — when the native UI is enabled — the
        # library browser, so the fixed tab count stays the same either way.
        self.main_stack = QStackedWidget()
        self.main_stack.addWidget(self.browser)
        if self.library_browser is not None:
            self.main_stack.addWidget(self.library_browser)

        # Add the Gameyfin tab with an empty string for the label
        gameyfin_tab_index = self.tab_widget.addTab(self.main_stack, "")

        # Remove close button from the main tab (index 0)
        self.tab_widget.tabBar().setTabButton(gameyfin_tab_index, QTabBar.ButtonPosition.RightSide, None)

        # Set the icon for that tab
        tab_icon = get_effective_icon(
            custom_path=self.settings.get("GF_ICON_PATH"),
            theme=self.settings.get("GF_THEME")
        )

        self.tab_widget.setTabIcon(gameyfin_tab_index, tab_icon)

        downloads_index = self.tab_widget.addTab(self.download_manager, "Downloads")
        self.tab_widget.tabBar().setTabButton(downloads_index, QTabBar.ButtonPosition.RightSide, None)

        prefixes_index = self.tab_widget.addTab(self.prefix_manager, "Prefixes")
        self.tab_widget.tabBar().setTabButton(prefixes_index, QTabBar.ButtonPosition.RightSide, None)

        settings_index = self.tab_widget.addTab(self.settings_widget, "Settings")
        self.tab_widget.tabBar().setTabButton(settings_index, QTabBar.ButtonPosition.RightSide, None)

        # Gamepad button hints live under the tabs and only appear once a
        # controller is actually connected.
        self.gamepad_hint_bar = GamepadHintBar()
        self.gamepad_hint_bar.hide()

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(self.tab_widget)
        container_layout.addWidget(self.gamepad_hint_bar)
        self.setCentralWidget(container)

        self.browser.page().profile().downloadRequested.connect(self.on_download_requested)

        # --- Loading overlay (initial load only) ---
        app_icon = get_effective_icon(
            custom_path=self.settings.get("GF_ICON_PATH"),
            theme=self.settings.get("GF_THEME"),
        )
        self._loading_overlay = LoadingOverlay(self, app_icon)
        self._position_overlay()
        self._initial_load_complete = False
        self._update_check_worker = None
        # Threads that would not stop in time; kept referenced so they are never
        # garbage collected while still running (only reachable at shutdown)
        self._retired_workers: list[Any] = []
        self.browser.loadStarted.connect(self._on_load_started)
        self.browser.loadFinished.connect(self._on_load_finished)

    def _inject_css(self) -> None:
        """Inject CSS to hide horizontal overflow in the browser."""
        script = QWebEngineScript()
        script.setSourceCode("""
            document.documentElement.style.overflowX = 'hidden';
            document.body.style.overflowX = 'hidden';
        """)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        self.browser.page().scripts().insert(script)

        # Gamepad navigation inside the page — installed on the profile so
        # every tab (including externally opened ones) gets it.
        self.profile.scripts().insert(build_nav_script())

    def _setup_gamepad(self) -> None:
        """Prepare gamepad support; nothing is constructed while it is disabled."""
        self.gamepad: GamepadManager | None = None
        self.gamepad_navigator: GamepadNavigator | None = None
        self._apply_gamepad_settings()

    def _create_gamepad(self) -> None:
        """Construct the manager and navigator on first use."""
        self.gamepad = GamepadManager(self.settings, self)
        self.gamepad_navigator = GamepadNavigator(
            self, self.gamepad, self.settings, hint_bar=self.gamepad_hint_bar
        )
        self.gamepad.connected.connect(self._on_gamepad_connected)
        self.gamepad.disconnected.connect(self._on_gamepad_disconnected)

    def _apply_gamepad_settings(self) -> None:
        """Start/stop polling and push the current settings into the gamepad stack.

        With gamepad support switched off nothing is created at all — no focus
        ring, no overlay, no focus-change hook — so the setting is a complete
        off switch rather than just muted input.
        """
        enabled = bool(self.settings.get("GF_GAMEPAD_ENABLED"))

        if not enabled:
            if self.gamepad is not None:
                self.gamepad.stop()
            if self.gamepad_navigator is not None:
                self.gamepad_navigator.set_enabled(False)
            self.settings_widget.set_gamepad_status("Disabled")
            self.gamepad_hint_bar.hide()
            return

        if self.gamepad is None:
            self._create_gamepad()

        self.gamepad.reload_settings()
        self.gamepad_navigator.reload_settings()
        self.gamepad_navigator.set_enabled(True)

        if not self.gamepad.start():
            self.settings_widget.set_gamepad_status("Gamepad support unavailable (SDL/pygame missing)")
        elif not self.gamepad.is_connected():
            self.settings_widget.set_gamepad_status("No controller detected")
        self._update_hint_bar_visibility()

    def _update_hint_bar_visibility(self) -> None:
        """Show the hint bar only when hints are enabled and a pad is connected."""
        show_hints = bool(self.settings.get("GF_GAMEPAD_HINTS"))
        connected = self.gamepad is not None and self.gamepad.is_connected()
        self.gamepad_hint_bar.setVisible(show_hints and connected)

    def _on_gamepad_connected(self, name: str) -> None:
        """Reflect a newly connected controller in the settings tab and hint bar."""
        self.settings_widget.set_gamepad_status(name)
        self._update_hint_bar_visibility()

    def _on_gamepad_disconnected(self) -> None:
        """Reflect controller removal in the settings tab and hint bar."""
        self.settings_widget.set_gamepad_status("No controller detected")
        self.gamepad_hint_bar.hide()

    def close_tab(self, index: int) -> None:
        """Close an external browser tab, preventing closure of the four fixed tabs."""
        # Prevent closing the fixed tabs (Main, Downloads, Prefixes, Settings)
        if index < FIXED_TAB_COUNT:
            return

        widget = self.tab_widget.widget(index)
        if widget:
            widget.deleteLater()
            self.tab_widget.removeTab(index)

    def _setup_new_view(self) -> QWebEngineView:
        """Create a new browser view with a CustomWebEnginePage and connect tab signals."""
        view = QWebEngineView()
        base_url = QUrl(self.settings.get("GF_URL"))
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
        """Create a new browser tab when the embedded page requests a new window.

        Args:
            _type: The window type requested by the web page.

        Returns:
            The QWebEnginePage of the new tab, or None.
        """
        view = self._setup_new_view()
        index = self.tab_widget.addTab(view, "Loading...")
        self.tab_widget.setCurrentIndex(index)
        return view.page()

    def handle_logout(self, url: QUrl) -> None:
        """Close all external tabs and navigate the main tab to the logout URL.

        Args:
            url: The logout URL to navigate to.
        """
        # Close all external tabs (starting from the end to avoid index shift issues)
        count = self.tab_widget.count()
        # Fixed tabs are 0 (Main), 1 (Downloads), 2 (Prefixes), 3 (Settings) - indices < FIXED_TAB_COUNT
        for i in range(count - 1, FIXED_TAB_COUNT - 1, -1):
            self.close_tab(i)

        # Ensure we are on the main tab, showing the web view so the user can log in again
        self.tab_widget.setCurrentIndex(0)
        if self.library_browser is not None:
            self._on_native_login_required()

        # Only navigate if the signal didn't come from the main page itself
        if self.sender() != self.browser.page():
            self.browser.setUrl(url)

    def redirect_to_main_tab(self, url: QUrl) -> None:
        """Switch to the main browser tab and navigate to the given URL.

        Also closes all external tabs that triggered the redirect.

        Args:
            url: The URL to navigate to on the main tab.
        """
        self.tab_widget.setCurrentIndex(0)
        self.browser.setUrl(url)
        # Close tabs after redirect to main tab
        sender_page = self.sender()
        if isinstance(sender_page, CustomWebEnginePage):
            count = self.tab_widget.count()
            for i in range(count - 1, FIXED_TAB_COUNT - 1, -1):
                self.close_tab(i)

    def _all_web_views(self) -> list[QWebEngineView]:
        """Return every web view in the window, including the one in the main stack."""
        views = [self.browser]
        for i in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(i)
            if isinstance(widget, QWebEngineView):
                views.append(widget)
        return views

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

    # ------------------------------------------------------------------
    # Loading overlay helpers
    # ------------------------------------------------------------------

    def _position_overlay(self) -> None:
        """Position the loading overlay over the central widget area."""
        if not hasattr(self, "_loading_overlay"):
            return
        # The overlay is a child widget, so its geometry is in the window's
        # client-area coordinate system (top-left = 0,0). self.rect() already
        # excludes window decorations, so it covers the client area exactly and
        # keeps the centered logo centered.
        self._loading_overlay.setGeometry(self.rect())

        navigator = getattr(self, "gamepad_navigator", None)
        if navigator is not None and navigator.help_overlay.isVisible():
            navigator.help_overlay.setGeometry(self.rect())

    def _on_load_started(self) -> None:
        """Show the loading overlay on initial page load."""
        if not self._initial_load_complete and self.tab_widget.currentIndex() < FIXED_TAB_COUNT:
            self._loading_overlay.show_overlay()

    def _on_load_finished(self, success: bool) -> None:
        """Hide the loading overlay after initial load completes."""
        if not self._initial_load_complete:
            self._initial_load_complete = True
            self._loading_overlay.hide_overlay()
            self._check_for_updates_on_startup()

        if success:
            self._maybe_activate_native_ui()

    # ------------------------------------------------------------------
    # Native library UI
    # ------------------------------------------------------------------

    def _native_ui_active(self) -> bool:
        """Return True when the native library is the widget on show in tab 0."""
        return (self.library_browser is not None
                and self.main_stack.currentWidget() is self.library_browser)

    def _probe_native_ui(self) -> None:
        """Try to load the library; only switch to it once the API accepts us.

        The web view stays in front until a fetch actually succeeds, so a probe
        that runs before login simply fails and is retried — the previous
        behaviour of switching first and falling back on 401 left the user on the
        web view forever, because Gameyfin's login emits no page-load signal.
        """
        if self.library_browser is None or not self._native_ui_requested():
            return
        if self._native_ui_active():
            return

        path = self.browser.url().path()
        if path.startswith("/login") or path.startswith("/setup"):
            # Still on the login page — keep polling, the redirect may be client-side
            self._native_probe_timer.start()
            return

        self.library_browser.refresh()

    def _on_native_library_loaded(self) -> None:
        """Bring the native library forward now that the session is proven good."""
        if self.library_browser is None or not self._native_ui_requested():
            return
        self._native_probe_timer.stop()
        self._native_cookie_timer.stop()
        self.main_stack.setCurrentWidget(self.library_browser)

    def _on_native_login_required(self) -> None:
        """Show the web view for login and keep probing until it succeeds."""
        self.show_login_view()
        if self._native_ui_requested():
            self._native_probe_timer.start()

    # Kept as the name used by the load-finished hook and the settings toggle
    def _maybe_activate_native_ui(self) -> None:
        """Probe for a usable session (see :meth:`_probe_native_ui`)."""
        self._probe_native_ui()

    def _apply_native_ui_setting(self) -> None:
        """Show or hide the native library UI to match ``GF_NATIVE_UI``.

        Turning the flag on builds the browser on first use; turning it off just
        brings the web view back to the front, so no restart is needed either way.
        """
        if not self._native_ui_requested():
            if self.library_browser is not None:
                self._native_probe_timer.stop()
                self._native_cookie_timer.stop()
                self.show_login_view()
            return

        if self.library_browser is None:
            self._build_native_ui()
            self.main_stack.addWidget(self.library_browser)

        self._probe_native_ui()
        if not self._native_ui_active():
            self._native_probe_timer.start()

    def show_login_view(self) -> None:
        """Bring the web view back to the front so the user can (re-)log in."""
        self.main_stack.setCurrentWidget(self.browser)

    def _on_native_download_requested(self, game: Any, provider_key: str) -> None:
        """Start a streaming download for a game picked in the native UI."""
        if self.api_client is None:
            return

        url = self.api_client.download_url(game.id, provider_key)
        basename = sanitize_name(game.title)
        target_dir = self._resolve_download_target(basename)
        if target_dir is None:
            return

        self._start_download(
            url=url,
            target_dir=target_dir,
            filename=f"{game.title}.zip",
            total_bytes=game.file_size,
        )

    def _check_for_updates_on_startup(self) -> None:
        """Check GitHub for a newer release once the initial load is done.

        Only opens the update dialog when a newer release exists; network
        errors and up-to-date results are silently ignored.
        """
        self._update_check_worker = UpdateCheckWorker()
        self._update_check_worker.finished.connect(self._on_startup_update_check)
        self._update_check_worker.start()

    @pyqtSlot(object, str)
    def _on_startup_update_check(self, release, error: str) -> None:
        """Open the update dialog when the startup check found a newer release."""
        # The worker is released separately: dropping the last reference here would
        # delete a QThread whose run() has not returned yet, which aborts the
        # process ("QThread: Destroyed while thread is still running").
        QTimer.singleShot(0, self._release_update_check_worker)

        if error or not release or not self.isVisible():
            return
        latest = release.get("tag_name", "").lstrip("vV")
        if compare_versions(latest, get_current_version()) <= 0:
            return
        dialog = UpdateDialog(self, self.settings, release=release)
        dialog.exec()

    def _release_update_check_worker(self) -> None:
        """Drop the update-check thread once it has actually stopped.

        ``wait()`` returns as soon as ``run()`` has left (it has already emitted
        its result by the time this is called), and ``deleteLater()`` lets Qt do
        the deletion from the event loop rather than from inside a signal.
        """
        worker = self._update_check_worker
        self._update_check_worker = None
        if worker is None:
            return

        if not worker.wait(3000):
            # Refused to stop (e.g. a hung network read): hold on to it rather
            # than let Python collect a live thread out from under Qt
            logger.warning("Update check thread did not stop; keeping it alive")
            self._retired_workers.append(worker)
            return

        worker.deleteLater()

    def resizeEvent(self, event: Any) -> None:  # type: ignore[override]
        """Reposition the overlay on window resize."""
        super().resizeEvent(event)
        self._position_overlay()

    def showEvent(self, event: Any) -> None:  # type: ignore[override]
        """Show the window; position overlay without showing loading screen after initial load."""
        super().showEvent(event)
        self._position_overlay()
        if not self._initial_load_complete:
            self._loading_overlay.show_overlay()

    def show_main_tab(self) -> None:
        """Show the window and switch to the main Gameyfin tab."""
        self.show()
        self.activateWindow()
        self.tab_widget.setCurrentWidget(self.main_stack)

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

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle window close: quit if ``is_really_quitting``, otherwise hide to tray.

        Args:
            event: The close event.
        """
        if self.is_really_quitting:
            # This is a real quit, run cleanup
            gamepad = getattr(self, "gamepad", None)
            if gamepad is not None:
                gamepad.stop()
            self._release_update_check_worker()
            self.download_manager.close()
            if self.library_browser is not None:
                self._native_probe_timer.stop()
                self._native_cookie_timer.stop()
                self.library_browser.close()
            if self.image_cache is not None:
                self.image_cache.shutdown()
            self.browser.setPage(None)
            self.browser.deleteLater()
            event.accept()
        else:
            # This is just the 'X' button, so hide
            event.ignore()
            self.hide()

    def _on_cookie_added(self, cookie) -> None:
        """Store an incoming cookie and re-probe the API if we are not native yet."""
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        value = bytes(cookie.value()).decode('utf-8', errors='replace')
        self._cookies[name] = value

        # A finished login shows up here first; debounce because several cookies
        # (session, CSRF, SSO state) land in quick succession.
        timer = getattr(self, "_native_cookie_timer", None)
        if timer is not None and not self._native_ui_active():
            timer.start()

    def _on_cookie_removed(self, cookie) -> None:
        """Remove a cookie from the internal cookie dict."""
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        self._cookies.pop(name, None)

    def _resolve_download_target(self, zip_basename: str) -> str | None:
        """Return the directory to extract a download into.

        Honours ``GF_DEFAULT_DOWNLOAD_DIR`` and ``GF_PROMPT_DOWNLOAD_DIR``, and
        always creates a per-game subfolder so removing a download never
        deletes the parent directory.

        Returns:
            The target directory, or None when the user cancelled the prompt.
        """
        default_download_dir = self.settings.get("GF_DEFAULT_DOWNLOAD_DIR")
        prompt_download = self.settings.get("GF_PROMPT_DOWNLOAD_DIR")

        if default_download_dir and os.path.exists(default_download_dir):
            target_base = default_download_dir
        else:
            target_base = os.path.expanduser("~/Downloads")

        if not prompt_download:
            return os.path.join(target_base, zip_basename)

        selected = QFileDialog.getExistingDirectory(
            self, "Select download location", target_base,
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not selected:
            return None
        if os.path.basename(selected) == zip_basename:
            return selected
        return os.path.join(selected, zip_basename)

    def _start_download(self, url: str, target_dir: str, filename: str,
                        total_bytes: int = 0) -> tuple[StreamDownloadWorker, dict[str, Any]]:
        """Queue a streaming download in the Downloads tab and switch to it.

        Args:
            url: The download URL (cookies from the web profile are attached).
            target_dir: Directory to extract into.
            filename: Display name for the download row.
            total_bytes: Known total size, or 0 when unknown.

        Returns:
            The worker driving the download and its history record.
        """
        record = {
            "path": target_dir,
            "filename": filename,
            "url": url,
            "status": "Downloading",
            "total_bytes": total_bytes,
        }
        bandwidth_limit = self.settings.get("GF_BANDWIDTH_LIMIT") or 0
        worker = StreamDownloadWorker(
            url, target_dir, dict(self._cookies),
            estimated_total=total_bytes, bandwidth_limit=bandwidth_limit
        )
        self.download_manager.add_download(worker, record)
        self.tab_widget.setCurrentWidget(self.download_manager)
        return worker, record

    def on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """Handle a download request from the web browser: determine target dir, spawn worker.

        Args:
            download: The QWebEngineDownloadRequest triggered by the web page.
        """
        url = download.url().toString()
        filename = os.path.basename(download.downloadFileName())
        zip_basename = sanitize_name(os.path.splitext(filename)[0])

        target_dir = self._resolve_download_target(zip_basename)
        if target_dir is None:
            download.cancel()
            return

        total_size = download.totalBytes() if download.totalBytes() > 0 else 0

        download.cancel()

        worker, record = self._start_download(url, target_dir, filename, total_size)

        # Older Gameyfin servers (pre-v2.4.1) don't send Content-Length, so
        # download.totalBytes() is unavailable too. Scrape the size shown on the
        # page as a fallback, without blocking the download from starting.
        if total_size <= 0:
            def handle_js_result(result):
                scraped_size = parse_size(result)
                if scraped_size > 0:
                    record["total_bytes"] = scraped_size
                    worker.estimated_total = scraped_size

            js = """(function() {
                let el = document.querySelector('button .text-xs');
                return el ? el.innerText : "";
            })();"""
            self.browser.page().runJavaScript(js, 0, handle_js_result)

    def apply_settings(self) -> None:
        """Apply settings dynamically without requiring a restart.

        Updates window geometry, browser URL, icon, UMU cache, and theme.
        """
        # 1. Update Window Geometry
        w = self.settings.get("GF_WINDOW_WIDTH")
        h = self.settings.get("GF_WINDOW_HEIGHT")
        if w and h:
            self.resize(w, h)

        # 2. Update Browser URL
        new_url_str = self.settings.get("GF_URL")
        if new_url_str:
            new_url = QUrl(new_url_str)
            new_host = new_url.host()
            if self.browser.url() != new_url:
                logger.info("Applying new URL: %s", new_url.toString())
                self.browser.setUrl(new_url)

            # Update the main_host in all custom pages
            for view in self._all_web_views():
                page = view.page()
                if isinstance(page, CustomWebEnginePage):
                    if page.restricted_host:
                        page.set_restricted_host(new_host)
                    else:
                        page.set_main_host(new_host)

            if self.api_client is not None:
                # A different server may use a different CSRF scheme
                self.api_client.reset_csrf()

        # 3. Update Icon
        app_icon = get_effective_icon(
            custom_path=self.settings.get("GF_ICON_PATH"),
            theme=self.settings.get("GF_THEME")
        )

        self.setWindowIcon(app_icon)
        # Update tab icon (index 0 is browser)
        self.tab_widget.setTabIcon(0, app_icon)

        # 4. Apply the native library UI flag without a restart
        self._apply_native_ui_setting()

        # 5. Refresh UMU Database (background, non-blocking)
        if sys.platform != "win32" and self.umu_database:
            self.umu_database.refresh_cache_async()

        # 6. Update Gamepad
        if hasattr(self, "gamepad"):
            self._apply_gamepad_settings()

        # 7. Update Theme
        theme = self.settings.get("GF_THEME")
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
