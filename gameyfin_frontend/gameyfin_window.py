import logging
import os
import sys
from typing import Any

from PyQt6.QtWidgets import (QMainWindow, QFileDialog, QTabWidget, QApplication, QTabBar,
                             QWidget, QComboBox)
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QStandardPaths, pyqtSignal, Qt, QTimer
from PyQt6.QtWebEngineCore import (QWebEngineScript,
                                   QWebEngineDownloadRequest, QWebEngineProfile, QWebEngineSettings, QWebEnginePage)

from qt_material import apply_stylesheet

from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
from gameyfin_frontend.widgets.loading_overlay import LoadingOverlay
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.umu_database import UmuDatabase

from .settings_widget import SettingsWidget
from .settings import SettingsManager
from .utils import get_effective_icon, parse_size
from .config import FIXED_TAB_COUNT

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
        self._setup_gamepad()
        self._inject_css()

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

    def _setup_tabs(self) -> None:
        """Initialize the tab widget and add all tabs."""
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

        self.setCentralWidget(self.tab_widget)

        self.browser.page().profile().downloadRequested.connect(self.on_download_requested)

        # ------------------------------------------------------------------
        # Gamepad focus tracking — build per-tab lists of focusable widgets
        # ------------------------------------------------------------------
        self._gamepad_focus_index: dict[int, int] = {}  # tab_index -> widget index
        self._active_combo: QComboBox | None = None  # combo box whose dropdown is currently open
        self._collect_focusable_widgets()
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        # --- Loading overlay (initial load only) ---
        app_icon = get_effective_icon(
            custom_path=self.settings.get("GF_ICON_PATH"),
            theme=self.settings.get("GF_THEME"),
        )
        self._loading_overlay = LoadingOverlay(self, app_icon)
        self._position_overlay()
        self._initial_load_complete = False
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

    # ------------------------------------------------------------------
    # Gamepad focus helpers
    # ------------------------------------------------------------------

    def _collect_focusable_widgets(self) -> None:
        """Build a list of focusable Qt widgets for each content tab.

        Skips the browser tab (index 0) because QWebEngineView swallows all
        key events.  Widgets with NoFocus are excluded; everything else that
        is visible and enabled is included so gamepad navigation works on
        buttons, combo boxes, spinboxes, etc.

        For tabs containing a QListWidget with setItemWidget items (e.g.
        PrefixManager), we walk the item widgets explicitly — Qt's internal
        storage for item widgets may not appear in findChildren().
        """
        from PyQt6.QtWidgets import QListWidget

        self._focusable_tabs: dict[int, list[QWidget]] = {}
        for idx in range(1, FIXED_TAB_COUNT):
            tab = self.tab_widget.widget(idx)
            if tab is None:
                continue

            focusable: list[QWidget] = []

            # --- Standard children ----------------------------------------
            for w in tab.findChildren(QWidget):
                if (w.focusPolicy() != Qt.FocusPolicy.NoFocus
                        and not w.isHidden()
                        and w.isEnabled()):
                    focusable.append(w)

            # --- QListWidget item widgets ---------------------------------
            # These are stored internally by the list widget and may not
            # show up as regular QObject descendants.
            list_w = getattr(tab, 'list_widget', None)
            if isinstance(list_w, QListWidget):
                for i in range(list_w.count()):
                    item_widget = list_w.itemWidget(list_w.item(i))
                    if item_widget is not None:
                        for child in item_widget.findChildren(QWidget):
                            if (child.focusPolicy() != Qt.FocusPolicy.NoFocus
                                    and not child.isHidden()
                                    and child.isEnabled()
                                    and child not in focusable):
                                focusable.append(child)

            # Sort by visual row then column for intuitive up/down/left/right
            focusable.sort(key=self._widget_sort_key)
            logger.info("Gamepad: tab %d has %d focusable widgets", idx, len(focusable))
            for i, w in enumerate(focusable):
                logger.info("  [%d] %s '%s' parent=%s", i, type(w).__name__, w.objectName(), type(w.parent()).__name__)
            if focusable:
                self._focusable_tabs[idx] = focusable
                self._gamepad_focus_index[idx] = 0
            else:
                logger.warning("Gamepad: no focusable widgets in tab %d!", idx)

    @staticmethod
    def _widget_sort_key(widget: QWidget) -> tuple[int, ...]:
        """Return a sort key based on the widget's visual position.

        Priority:
        1. QListWidget row index + child position within item widget
        2. Layout position (QGridLayout → row,col; QVBoxLayout/HBoxLayout → index)
        3. Fallback to ``(0,)``
        """
        from PyQt6.QtWidgets import QListWidget

        # Check if widget is inside a QListWidget item by walking up the
        # parent chain looking for a QListWidget ancestor.
        ancestor = widget.parent()
        while ancestor is not None and isinstance(ancestor, QWidget):
            if isinstance(ancestor, QListWidget):
                for i in range(ancestor.count()):
                    item = ancestor.item(i)
                    if item is None:
                        continue
                    item_widget = ancestor.itemWidget(item)
                    if item_widget is not None and item_widget is widget:
                        return (i, 0)
                    if item_widget is not None:
                        children = list(item_widget.findChildren(QWidget))
                        # Sort children by layout position for correct left-to-right ordering
                        try:
                            ilayout = item_widget.layout()
                            if ilayout is not None:
                                child_positions = []
                                for ch in children:
                                    p = ilayout.indexOf(ch)
                                    child_positions.append((p if p >= 0 else 999, ch))
                                child_positions.sort(key=lambda x: x[0])
                                children = [ch for _, ch in child_positions]
                        except (TypeError, AttributeError):
                            pass
                        if widget in children:
                            col_idx = children.index(widget)
                            return (i, col_idx)
                break  # Not found in any item of this QListWidget
            ancestor = ancestor.parent()

        # Layout-based positioning for non-list widgets
        parent = widget.parent()
        if parent is None or not isinstance(parent, QWidget):
            return (0,)

        try:
            layout = parent.layout()
        except (TypeError, AttributeError):
            layout = None

        if layout is not None:
            pos = layout.indexOf(widget)
            if pos >= 0:
                try:
                    r, c, _, _ = layout.cellPosition(pos)
                    return (r, c)
                except AttributeError:
                    return (pos, 0)

        return (0,)

    def _set_gamepad_focus(self, index: int) -> None:
        """Give keyboard/gamepad focus to the widget at *index* in the current tab."""
        tab_idx = self.tab_widget.currentIndex()
        widgets = self._focusable_tabs.get(tab_idx)
        if not widgets:
            return
        if 0 <= index < len(widgets):
            target = widgets[index]
            target.setFocus()
            self._gamepad_focus_index[tab_idx] = index

    def _on_tab_changed(self, idx: int) -> None:
        """Reset gamepad focus when the user switches tabs (mouse or gamepad)."""
        self._active_combo = None
        if idx in self._focusable_tabs:
            QTimer.singleShot(50, lambda i=idx: self._set_gamepad_focus(0))

    # ------------------------------------------------------------------
    # Gamepad support
    # ------------------------------------------------------------------

    def _setup_gamepad(self) -> None:
        """Schedule gamepad setup after Qt finishes its Wayland/GL init.

        Importing pyglet/SDL2 too early corrupts Qt's Wayland GL context
        (EGL errors).  We defer via QTimer.singleShot so Qt has already
        created its surfaces before SDL2 touches the GPU.
        """
        if sys.platform == "win32":
            logger.debug("Gamepad support disabled on Windows.")
            return

        enabled = bool(self.settings.get("GF_GAMEPAD_ENABLED"))
        if not enabled:
            logger.debug("Gamepad support disabled by user setting.")
            return

        # Defer actual init — see docstring for why (SDL2 touches GPU before
        # Qt finishes its Wayland EGL setup with 0-delay).
        QTimer.singleShot(500, self._init_gamepad)

    def _init_gamepad(self) -> None:
        """Lazily create and start the GamepadManager + poll timer."""
        from .gamepad import GamepadManager  # noqa: local import to avoid early SDL2 init

        try:
            import pyglet  # noqa: F401 — verify availability
        except ImportError:
            logger.warning("Gamepad support: pyglet not available.")
            return

        self.gamepad = GamepadManager(self)
        self.gamepad.start()

        # Poll gamepad state every 100 ms
        self._gamepad_timer = QTimer(self)
        self._gamepad_timer.setInterval(100)
        self._gamepad_timer.timeout.connect(self._poll_gamepad)
        self._gamepad_timer.start()

        logger.info("GamepadManager initialized and polling started.")

    def _poll_gamepad(self) -> None:
        """Poll gamepad state and dispatch actions (called every 100 ms)."""
        BUTTON_LABELS = {
            "a": ("A (Confirm)", self._handle_confirm),
            "b": ("B (Cancel)", self._handle_cancel),
            "y": ("Y (Context Menu)", self._handle_context_menu),
            "leftshoulder": ("L1 (Prev Tab)", lambda: self._handle_tab_switch(-1)),
            "rightshoulder": ("R1 (Next Tab)", lambda: self._handle_tab_switch(1)),
        }

        widget = QApplication.focusWidget()

        # -- Combo box popup handling -----------------------------------------
        # When a QComboBox dropdown is open, stick input navigates items
        # instead of changing focus. A selects, B closes.
        # We track _active_combo directly because Qt gives focus to the
        # popup's internal list, so focusWidget() won't be a QComboBox.
        cb = self._active_combo
        if cb is not None:
            direction = self.gamepad.get_new_navigation_direction()
            if direction:
                logger.info("Gamepad: combo-box nav → %s", direction)
                self._navigate_combo_box_item(direction)
                return

            # A selects the highlighted item
            if self.gamepad.was_pressed("a"):
                logger.info("Gamepad: A pressed → select combo-box item")
                self._select_combo_box_item(cb)
                return

            # B closes the popup without selecting
            if self.gamepad.was_pressed("b"):
                logger.info("Gamepad: B pressed → close combo-box popup")
                cb.hidePopup()
                # Clear navigating flag so future activations work normally
                GameyfinWindow._set_navigating(self, cb, False)
                self._active_combo = None
                return

        # -- Normal navigation ------------------------------------------------
        direction = self.gamepad.get_new_navigation_direction()
        if direction:
            logger.info("Gamepad: stick/D-pad → %s", direction)
            self._handle_navigation(direction)
            return  # one action per poll cycle

        # Action buttons (edge-triggered via was_pressed)
        for button, (label, handler) in BUTTON_LABELS.items():
            if self.gamepad.was_pressed(button):
                logger.info("Gamepad: %s pressed", label)
                handler()
                break  # only one action per poll cycle

    # -- action handlers (same logic as before, just renamed) ---------------

    def _handle_navigation(self, direction: str) -> None:
        """Navigate between focusable widgets in the active content tab."""
        tab_idx = self.tab_widget.currentIndex()
        widgets = self._focusable_tabs.get(tab_idx)
        if not widgets:
            return

        idx = self._gamepad_focus_index.get(tab_idx, 0)
        count = len(widgets)

        delta = -1 if direction in ("up", "left") else 1
        new_idx = (idx + delta) % count
        self._set_gamepad_focus(new_idx)

    def _handle_confirm(self) -> None:
        """Activate the currently focused widget (A / X button)."""
        widget = QApplication.focusWidget()
        if widget is None:
            return

        # Open combo-box dropdown instead of sending Return
        if isinstance(widget, QComboBox):
            self._open_combo_box_popup(widget)
            return

        from PyQt6.QtWidgets import QAbstractButton

        if isinstance(widget, QAbstractButton):
            widget.click()
            return

        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent

        for etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            event = QKeyEvent(etype, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget, event)

    def _handle_cancel(self) -> None:
        """Simulate Escape key press on the currently focused widget."""
        widget = QApplication.focusWidget()
        if widget is None:
            return
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)

    def _handle_context_menu(self) -> None:
        """Show context menu on the currently focused widget (if applicable)."""
        widget = QApplication.focusWidget()
        if widget is None:
            return
        from PyQt6.QtGui import QContextMenuEvent

        pos = widget.rect().center()
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Keyboard,
            pos,
            widget.mapToGlobal(pos),
        )
        QApplication.sendEvent(widget, event)

    # ------------------------------------------------------------------
    # Combo box helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _set_navigating(self_ref: Any, cb: QComboBox, value: bool) -> None:
        """Set/clear the navigating flag on the combo box's owning widget."""
        parent = cb.parent()
        nav = getattr(parent, "_navigating_popup", None)
        if nav is not None:
            parent._navigating_popup = value

    def _open_combo_box_popup(self, cb: QComboBox) -> None:
        """Open a QComboBox dropdown so the user can pick an item."""
        logger.info("Gamepad: opening combo-box popup (%d items)", cb.count())
        # Mark as navigating so activated signal doesn't launch prematurely
        GameyfinWindow._set_navigating(self, cb, True)
        cb.showPopup()
        self._active_combo = cb

    def _navigate_combo_box_item(self, direction: str) -> None:
        """Move the highlight inside an open QComboBox dropdown using stick input.

        Drives selection directly via setCurrentIndex — avoids relying on
        synthetic key events reaching a popup that may not have native focus.
        """
        cb = self._active_combo
        if cb is None:
            return
        current = cb.currentIndex()
        count = cb.count()
        if direction in ("up", "left"):
            new_idx = max(0, current - 1)
        else:
            new_idx = min(count - 1, current + 1)
        if new_idx != current:
            cb.setCurrentIndex(new_idx)

    def _select_combo_box_item(self, cb: QComboBox) -> None:
        """Select the currently highlighted item and close the popup."""
        index = cb.currentIndex()
        text = cb.currentText()
        # Clear navigating flag so launch_script actually fires
        GameyfinWindow._set_navigating(self, cb, False)
        parent = cb.parent()
        launch = getattr(parent, "launch_script", None)
        if callable(launch):
            launch(index)
        else:
            cb.hidePopup()
            self._active_combo = None
            logger.info("Gamepad: selected combo-box item %d — '%s'", index, text)

    def _handle_tab_switch(self, direction: int) -> None:
        """Switch tabs using L1/R1 — skips the WebEngine tab (index 0)."""
        nav_count = FIXED_TAB_COUNT - 1
        if nav_count < 1:
            return

        start = self.tab_widget.currentIndex()
        rel = max(start, 1) - 1
        new_idx = (rel + direction) % nav_count + 1

        if new_idx != start:
            self.tab_widget.setCurrentIndex(new_idx)
            QTimer.singleShot(50, lambda i=new_idx: self._set_gamepad_focus(0))

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

        # Ensure we are on the main tab
        self.tab_widget.setCurrentIndex(0)

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

    def _on_load_started(self) -> None:
        """Show the loading overlay on initial page load."""
        if not self._initial_load_complete and self.tab_widget.currentIndex() < FIXED_TAB_COUNT:
            self._loading_overlay.show_overlay()

    def _on_load_finished(self, success: bool) -> None:
        """Hide the loading overlay after initial load completes."""
        if not self._initial_load_complete:
            self._initial_load_complete = True
            self._loading_overlay.hide_overlay()

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

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle window close: quit if ``is_really_quitting``, otherwise hide to tray.

        Args:
            event: The close event.
        """
        if self.is_really_quitting:
            # This is a real quit, run cleanup
            if hasattr(self, "gamepad"):
                self.gamepad.stop()
            if hasattr(self, "_gamepad_timer"):
                self._gamepad_timer.stop()
            self.download_manager.close()
            self.browser.setPage(None)
            self.browser.deleteLater()
            event.accept()
        else:
            # This is just the 'X' button, so hide
            event.ignore()
            self.hide()

    def _on_cookie_added(self, cookie) -> None:
        """Store an incoming cookie in the internal cookie dict."""
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        value = bytes(cookie.value()).decode('utf-8', errors='replace')
        self._cookies[name] = value

    def _on_cookie_removed(self, cookie) -> None:
        """Remove a cookie from the internal cookie dict."""
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        self._cookies.pop(name, None)

    def on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """Handle a download request from the web browser: determine target dir, spawn worker.

        Args:
            download: The QWebEngineDownloadRequest triggered by the web page.
        """
        url = download.url().toString()
        filename = os.path.basename(download.downloadFileName())
        zip_basename = os.path.splitext(filename)[0]

        default_download_dir = self.settings.get("GF_DEFAULT_DOWNLOAD_DIR")
        prompt_download = self.settings.get("GF_PROMPT_DOWNLOAD_DIR")

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
            total_size = parse_size(result)
            record = {
                "path": target_dir,
                "filename": filename,
                "url": url,
                "status": "Downloading",
                "total_bytes": total_size,
            }
            bandwidth_limit = self.settings.get("GF_BANDWIDTH_LIMIT") or 0
            worker = StreamDownloadWorker(url, target_dir, cookies, estimated_total=total_size, bandwidth_limit=bandwidth_limit)
            self.download_manager.add_download(worker, record)
            self.tab_widget.setCurrentWidget(self.download_manager)

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
            custom_path=self.settings.get("GF_ICON_PATH"),
            theme=self.settings.get("GF_THEME")
        )

        self.setWindowIcon(app_icon)
        # Update tab icon (index 0 is browser)
        self.tab_widget.setTabIcon(0, app_icon)

        # 4. Refresh UMU Database
        if sys.platform != "win32" and self.umu_database:
            self.umu_database.refresh_cache()

        # 5. Update Theme
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
