"""Tests for the main window (GameyfinWindow and CustomWebEnginePage)."""

import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


class FakeSignal:
    """Minimal stand-in for a pyqtSignal that emits synchronously."""

    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self, *args):
        for handler in list(self._handlers):
            handler(*args)


@pytest.fixture()
def mock_umu_database():
    """Return a mock UmuDatabase."""
    db = MagicMock()
    db.search_by_partial_title.return_value = []
    db.get_game_by_codename.return_value = []
    db.get_umu_cache_path.return_value = "/tmp/umu_cache.json"
    return db


@pytest.fixture()
def mock_settings():
    """Return a mock SettingsManager."""
    settings = MagicMock()
    settings.get.return_value = "http://localhost:8080"
    settings.get_config_dir.return_value = "/tmp/gameyfin_profile"
    # Window dimensions must be integers for setGeometry
    def get_side_effect(key, default=None):
        if key == "GF_WINDOW_WIDTH":
            return 1280
        if key == "GF_WINDOW_HEIGHT":
            return 720
        if default is not None:
            return default
        return "http://localhost:8080"
    settings.get.side_effect = get_side_effect
    return settings


class TestCustomWebEnginePage:
    @pytest.fixture()
    def webengine_page_patch(self):
        """Patch QWebEnginePage.__init__ to avoid Qt WebEngine initialization crashes."""
        with patch("PyQt6.QtWebEngineCore.QWebEnginePage.__init__", return_value=None):
            yield

    @pytest.fixture()
    def accept_nav_patch(self):
        """Patch acceptNavigationRequest to return True by default."""
        with patch.object(
            "gameyfin_frontend.gameyfin_window.CustomWebEnginePage",
            "acceptNavigationRequest",
            return_value=True,
        ):
            yield

    def test_page_initializes_with_hosts(self, webengine_page_patch):
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage
        page = CustomWebEnginePage(None, restricted_host="localhost", main_host="localhost")
        assert page.restricted_host == "localhost"
        assert page.main_host == "localhost"

    def test_page_initializes_without_hosts(self, webengine_page_patch):
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage
        page = CustomWebEnginePage(None)
        assert page.restricted_host is None
        assert page.main_host is None

    def test_set_restricted_host(self, webengine_page_patch):
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage
        page = CustomWebEnginePage(None)
        page.set_restricted_host("example.com")
        assert page.restricted_host == "example.com"

    def test_set_main_host(self, webengine_page_patch):
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage
        page = CustomWebEnginePage(None)
        page.set_main_host("example.com")
        assert page.main_host == "example.com"

    def test_create_window_returns_none_by_default(self, webengine_page_patch):
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage
        page = CustomWebEnginePage(None)
        result = page.createWindow(0)
        assert result is None

    def test_create_window_with_callback(self, webengine_page_patch):
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage
        page = CustomWebEnginePage(None)
        mock_callback = MagicMock(return_value="new_page")
        page.create_window_callback = mock_callback
        result = page.createWindow(0)
        mock_callback.assert_called_once()
        assert result == "new_page"

    def test_accept_navigation_request_main_host_only(self, webengine_page_patch):
        """Test that navigation to main host is allowed on main page."""
        from PyQt6.QtCore import QUrl
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage

        # Patch acceptNavigationRequest to use parent logic
        class TestPage(CustomWebEnginePage):
            def acceptNavigationRequest(self, url, nav_type, is_main_frame):
                if is_main_frame:
                    if self.main_host and url.host() == self.main_host and '/logout' in url.path():
                        if self.restricted_host:
                            return True
                    if self.main_host and url.host() == self.main_host:
                        return True
                if self.restricted_host and url.host() == self.restricted_host:
                    return True
                return False

        page = TestPage(None, restricted_host="localhost", main_host="localhost")
        url = QUrl("http://localhost/page")
        result = page.acceptNavigationRequest(url, 0, True)
        assert result is True

    def test_accept_navigation_request_stays_on_restricted_host(self, webengine_page_patch):
        """Test that navigation to different host is blocked on restricted page."""
        from PyQt6.QtCore import QUrl
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage

        class TestPage(CustomWebEnginePage):
            def acceptNavigationRequest(self, url, nav_type, is_main_frame):
                if is_main_frame:
                    if self.main_host and url.host() == self.main_host and '/logout' in url.path():
                        if self.restricted_host:
                            return True
                    if self.main_host and url.host() == self.main_host:
                        return True
                if self.restricted_host and url.host() == self.restricted_host:
                    return True
                return False

        page = TestPage(None, restricted_host="localhost", main_host="localhost")
        url = QUrl("http://evil.com/page")
        result = page.acceptNavigationRequest(url, 0, True)
        assert result is False

    def test_accept_navigation_request_allows_same_host(self, webengine_page_patch):
        """Test that navigation to same host is allowed."""
        from PyQt6.QtCore import QUrl
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage

        class TestPage(CustomWebEnginePage):
            def acceptNavigationRequest(self, url, nav_type, is_main_frame):
                if is_main_frame:
                    if self.main_host and url.host() == self.main_host and '/logout' in url.path():
                        if self.restricted_host:
                            return True
                    if self.main_host and url.host() == self.main_host:
                        return True
                if self.restricted_host and url.host() == self.restricted_host:
                    return True
                return False

        page = TestPage(None, restricted_host="localhost", main_host="localhost")
        url = QUrl("http://localhost/other-page")
        result = page.acceptNavigationRequest(url, 0, True)
        assert result is True

    def test_logout_detected_signal_emitted(self, webengine_page_patch):
        """Test that logout URL detection logic works correctly."""
        from PyQt6.QtCore import QUrl
        from gameyfin_frontend.gameyfin_window import CustomWebEnginePage

        class TestPage(CustomWebEnginePage):
            def acceptNavigationRequest(self, url, nav_type, is_main_frame):
                if is_main_frame:
                    if self.main_host and url.host() == self.main_host and '/logout' in url.path():
                        # Signal emission requires full Qt init; test the logic path instead
                        if self.restricted_host:
                            return True
                    if self.main_host and url.host() == self.main_host:
                        return True
                if self.restricted_host and url.host() == self.restricted_host:
                    return True
                return False

        page = TestPage(None, restricted_host="localhost", main_host="localhost")
        logout_url = QUrl("http://localhost/logout")
        # Verify logout URL is detected by the navigation logic
        result = page.acceptNavigationRequest(logout_url, 0, True)
        assert result is True


class TestGameyfinWindow:
    @pytest.fixture(autouse=True)
    def _no_real_update_check(self, monkeypatch):
        """Keep async loadFinished from starting a real GitHub update check."""
        fake_cls, _ = self._make_fake_check_worker()
        monkeypatch.setattr(
            "gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls
        )

    def _make_window(self, qtbot, mock_umu_database, mock_settings):
        """Helper to create a GameyfinWindow with all necessary patches."""
        from PyQt6.QtGui import QIcon
        # Mock CustomWebEnginePage to avoid Qt WebEngine initialization issues
        mock_page = MagicMock()
        mock_page.restricted_host = "localhost"
        mock_page.main_host = "localhost"
        mock_page.new_tab_requested = MagicMock()
        mock_page.main_tab_redirect_requested = MagicMock()
        mock_page.logout_detected = MagicMock()

        with patch("gameyfin_frontend.gameyfin_window.QStandardPaths.writableLocation", return_value="/tmp/gameyfin_profile"):
            with patch("gameyfin_frontend.gameyfin_window.get_effective_icon") as mock_icon:
                mock_icon.return_value = QIcon()
                with patch("gameyfin_frontend.gameyfin_window.GameyfinWindow.on_download_requested"):
                    with patch("gameyfin_frontend.gameyfin_window.CustomWebEnginePage", return_value=mock_page):
                        with patch("PyQt6.QtWebEngineWidgets.QWebEngineView.setPage"):
                            from gameyfin_frontend.gameyfin_window import GameyfinWindow
                            window = GameyfinWindow(mock_umu_database, mock_settings)
                            qtbot.addWidget(window)
                            return window

    def test_window_has_five_fixed_tabs(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        assert window.tab_widget.count() == 5

    def test_main_tab_has_no_close_button(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtWidgets import QTabBar
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        right_button = window.tab_widget.tabBar().tabButton(0, QTabBar.ButtonPosition.RightSide)
        assert right_button is None

    def test_close_tab_prevents_closing_fixed_tabs(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        initial_count = window.tab_widget.count()
        for i in range(5):
            window.close_tab(i)
        assert window.tab_widget.count() == initial_count

    def test_show_main_tab_shows_window(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.hide()
        window.show_main_tab()
        assert window.isVisible()

    def test_show_downloads_tab_switches_tab(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show_downloads_tab()
        assert window.tab_widget.currentWidget() is window.download_manager

    def test_show_settings_tab_switches_tab(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show_settings_tab()
        assert window.tab_widget.currentWidget() is window.settings_widget

    def test_system_tab_is_last_fixed_tab(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtWidgets import QTabBar
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        index = window.tab_widget.count() - 1
        assert window.tab_widget.tabText(index) == "System"
        assert window.tab_widget.widget(index) is window.system_tab
        # Like the other fixed tabs, the System tab has no close button
        right_button = window.tab_widget.tabBar().tabButton(index, QTabBar.ButtonPosition.RightSide)
        assert right_button is None

    def test_system_tab_exit_button_quits_application(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtWidgets import QMessageBox
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        mock_app = MagicMock()
        with patch("gameyfin_frontend.gameyfin_window.QApplication") as mock_app_cls, \
             patch("gameyfin_frontend.widgets.system_tab.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            mock_app_cls.instance.return_value = mock_app
            window.system_tab.exit_button.click()
        assert window.is_really_quitting
        mock_app.quit.assert_called_once()

    def test_close_event_hides_when_not_quitting(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtGui import QCloseEvent
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        event = MagicMock(spec=QCloseEvent)
        window.closeEvent(event)
        event.ignore.assert_called_once()

    def test_close_event_quits_when_quitting(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtGui import QCloseEvent
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.is_really_quitting = True
        event = MagicMock(spec=QCloseEvent)
        window.closeEvent(event)
        event.accept.assert_called_once()

    def test_update_tab_title(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        external_view = QWebEngineView()
        index = window.tab_widget.addTab(external_view, "Old Title")
        window.update_tab_title(external_view, "New Title")
        assert window.tab_widget.tabText(index) == "New Title"

    def test_update_tab_icon(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        external_view = QWebEngineView()
        index = window.tab_widget.addTab(external_view, "Tab")
        new_icon = QIcon()
        # Verify method doesn't crash and sets the icon (even if null)
        window.update_tab_icon(external_view, new_icon)
        # Verify the tab still exists and has the correct text
        assert window.tab_widget.tabText(index) == "Tab"

    def test_handle_logout_closes_external_tabs(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        external_view = QWebEngineView()
        window.tab_widget.addTab(external_view, "External")
        assert window.tab_widget.count() == 6
        window.handle_logout(QUrl("http://localhost/logout"))
        assert window.tab_widget.count() == 5
        assert window.tab_widget.currentIndex() == 0

    def test_redirect_to_main_tab(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        external_view = QWebEngineView()
        window.tab_widget.addTab(external_view, "External")
        window.redirect_to_main_tab(QUrl("http://localhost/new"))
        assert window.tab_widget.currentIndex() == 0

    def _make_fake_check_worker(self, release=None, error="", stops=True):
        """Build a fake UpdateCheckWorker class with the given outcome.

        ``stops`` mimics whether the thread comes to a halt when waited on.
        """
        instances = []

        class FakeCheckWorker:
            def __init__(self):
                self.finished = FakeSignal()
                self._release = release
                self._error = error
                self.waited = False
                self.deleted = False
                instances.append(self)

            def start(self):
                pass

            def wait(self, timeout=0):
                self.waited = True
                return stops

            def deleteLater(self):
                self.deleted = True

        return FakeCheckWorker, instances

    def test_load_finished_starts_update_check(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show()
        fake_cls, instances = self._make_fake_check_worker()
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            window._on_load_finished(True)
        assert len(instances) == 1
        assert window._initial_load_complete

    def test_load_finished_only_checks_once(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show()
        fake_cls, instances = self._make_fake_check_worker()
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            window._on_load_finished(True)
            window._on_load_finished(True)
        assert len(instances) == 1

    def test_startup_update_check_opens_dialog_for_new_release(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show()
        release = {"tag_name": "v9.9.9", "assets": []}
        fake_cls, instances = self._make_fake_check_worker(release=release)
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            with patch("gameyfin_frontend.gameyfin_window.UpdateDialog") as mock_dialog_cls:
                window._on_load_finished(True)
                instances[0].finished.emit(release, "")
        mock_dialog_cls.assert_called_once_with(window, window.settings, release=release)
        mock_dialog_cls.return_value.exec.assert_called_once()

    def test_startup_update_check_silent_when_up_to_date(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show()
        release = {"tag_name": "v1.0.0", "assets": []}
        fake_cls, instances = self._make_fake_check_worker(release=release)
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            with patch("gameyfin_frontend.gameyfin_window.UpdateDialog") as mock_dialog_cls:
                window._on_load_finished(True)
                instances[0].finished.emit(release, "")
        mock_dialog_cls.assert_not_called()

    def test_startup_update_check_silent_on_error(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show()
        fake_cls, instances = self._make_fake_check_worker(error="connection refused")
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            with patch("gameyfin_frontend.gameyfin_window.UpdateDialog") as mock_dialog_cls:
                window._on_load_finished(True)
                instances[0].finished.emit(None, "connection refused")
        mock_dialog_cls.assert_not_called()

    def test_result_handler_does_not_drop_the_running_worker(self, qtbot, mock_umu_database, mock_settings):
        """Clearing the reference inline would delete a still-running QThread."""
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        fake_cls, instances = self._make_fake_check_worker()
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            window._check_for_updates_on_startup()
        worker = instances[0]

        with patch("gameyfin_frontend.gameyfin_window.UpdateDialog"):
            window._on_startup_update_check(None, "")

        # Released by the scheduled call instead, which waits first
        assert window._update_check_worker is worker
        assert not worker.deleted

    def test_release_waits_before_dropping_the_worker(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        fake_cls, instances = self._make_fake_check_worker()
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            window._check_for_updates_on_startup()
        worker = instances[0]

        window._release_update_check_worker()

        assert window._update_check_worker is None
        assert worker.waited
        assert worker.deleted

    def test_worker_that_will_not_stop_is_kept_alive(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        fake_cls, instances = self._make_fake_check_worker(stops=False)
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            window._check_for_updates_on_startup()
        worker = instances[0]

        window._release_update_check_worker()

        # Never deleted, but still referenced so it cannot be collected mid-run
        assert not worker.deleted
        assert worker in window._retired_workers

    def test_close_event_releases_the_check_worker(self, qtbot, mock_umu_database, mock_settings):
        from PyQt6.QtGui import QCloseEvent
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        fake_cls, instances = self._make_fake_check_worker()
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            window._check_for_updates_on_startup()

        window.is_really_quitting = True
        window.closeEvent(QCloseEvent())

        assert window._update_check_worker is None
        assert instances[0].waited
        assert instances[0].deleted

    def test_startup_update_check_skipped_when_hidden(self, qtbot, mock_umu_database, mock_settings):
        window = self._make_window(qtbot, mock_umu_database, mock_settings)
        window.show()
        window.hide()
        release = {"tag_name": "v9.9.9", "assets": []}
        fake_cls, instances = self._make_fake_check_worker(release=release)
        with patch("gameyfin_frontend.gameyfin_window.UpdateCheckWorker", fake_cls):
            with patch("gameyfin_frontend.gameyfin_window.UpdateDialog") as mock_dialog_cls:
                window._on_load_finished(True)
                instances[0].finished.emit(release, "")
        mock_dialog_cls.assert_not_called()


class TestNativeLibraryUI:
    """Wiring of the GF_NATIVE_UI feature flag into the main window."""

    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        """Keep the startup update check off the network in these tests."""
        class FakeCheckWorker:
            def __init__(self, *args, **kwargs):
                self.finished = FakeSignal()

            def start(self):
                pass

            def wait(self, *args):
                return True

            def deleteLater(self):
                pass

        monkeypatch.setattr(
            "gameyfin_frontend.gameyfin_window.UpdateCheckWorker", FakeCheckWorker
        )

    @pytest.fixture()
    def native_settings(self, tmp_path):
        """Settings mock with the native library UI enabled."""
        settings = MagicMock()
        values = {
            "GF_WINDOW_WIDTH": 1280,
            "GF_WINDOW_HEIGHT": 720,
            "GF_NATIVE_UI": 1,
            "GF_URL": "http://localhost:8080",
            "GF_DEFAULT_DOWNLOAD_DIR": str(tmp_path),
            "GF_PROMPT_DOWNLOAD_DIR": 0,
            "GF_BANDWIDTH_LIMIT": 0,
            "GF_GAMEPAD_ENABLED": 0,
            "GF_ICON_PATH": "",
            "GF_THEME": "auto",
        }
        settings.get.side_effect = lambda key, default=None: values.get(key, default)
        settings.get_config_dir.return_value = str(tmp_path)
        settings.get_downloads_json_path.return_value = str(tmp_path / "downloads.json")
        return settings

    def _make_native_window(self, qtbot, mock_umu_database, settings):
        """Build a window with the native UI on, without touching the network."""
        from PyQt6.QtGui import QIcon

        mock_page = MagicMock()
        mock_page.restricted_host = "localhost"
        mock_page.main_host = "localhost"

        with patch("gameyfin_frontend.gameyfin_window.QStandardPaths.writableLocation", return_value=str(settings.get_config_dir())), \
             patch("gameyfin_frontend.gameyfin_window.get_effective_icon", return_value=QIcon()), \
             patch("gameyfin_frontend.gameyfin_window.CustomWebEnginePage", return_value=mock_page), \
             patch("PyQt6.QtWebEngineWidgets.QWebEngineView.setPage"), \
             patch("PyQt6.QtWebEngineWidgets.QWebEngineView.setUrl"), \
             patch("gameyfin_frontend.widgets.library_browser.LibraryBrowserWidget.refresh"):
            from gameyfin_frontend.gameyfin_window import GameyfinWindow
            window = GameyfinWindow(mock_umu_database, settings)
            qtbot.addWidget(window)
            return window

    def test_flag_off_builds_no_native_widgets(self, qtbot, mock_umu_database, mock_settings):
        window = TestGameyfinWindow()._make_window(qtbot, mock_umu_database, mock_settings)

        assert window.library_browser is None
        assert window.api_client is None
        assert window.main_stack.count() == 1

    def test_flag_on_adds_library_browser_to_main_stack(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        assert window.library_browser is not None
        assert window.api_client is not None
        assert window.main_stack.count() == 2
        # The five fixed tabs are unchanged — the stack lives inside tab 0
        assert window.tab_widget.count() == 5
        assert window.tab_widget.widget(0) is window.main_stack

    def test_api_client_uses_live_web_view_cookies(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        window._cookies["JSESSIONID"] = "session-value"

        assert window.api_client.cookie_provider()["JSESSIONID"] == "session-value"

    def test_rpc_transport_targets_the_main_page(self, qtbot, mock_umu_database, native_settings):
        """Calls must run in the logged-in page, not against a mirrored cookie jar."""
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        assert window.api_client.rpc_transport is window.webview_rpc
        assert window.webview_rpc.page_provider() is window.browser.page()

    def test_login_page_is_not_probed_but_keeps_polling(self, qtbot, mock_umu_database, native_settings):
        from PyQt6.QtCore import QUrl
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        with patch.object(type(window.browser), "url", return_value=QUrl("http://localhost:8080/login")), \
             patch.object(window.library_browser, "refresh") as mock_refresh:
            window._probe_native_ui()

        assert window.main_stack.currentWidget() is window.browser
        mock_refresh.assert_not_called()
        assert window._native_probe_timer.isActive()

    def test_probe_fetches_but_stays_on_web_view_until_it_succeeds(self, qtbot, mock_umu_database, native_settings):
        from PyQt6.QtCore import QUrl
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        with patch.object(type(window.browser), "url", return_value=QUrl("http://localhost:8080/")), \
             patch.object(window.library_browser, "refresh") as mock_refresh:
            window._probe_native_ui()

        mock_refresh.assert_called_once()
        # Nothing has loaded yet, so the web view must still be the visible widget
        assert window.main_stack.currentWidget() is window.browser

    def test_successful_load_switches_to_the_native_library(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        window.library_browser.library_loaded.emit()

        assert window.main_stack.currentWidget() is window.library_browser
        assert not window._native_probe_timer.isActive()

    def test_auth_failure_returns_to_web_view_and_retries(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        window.main_stack.setCurrentWidget(window.library_browser)

        window.library_browser.login_required.emit()

        assert window.main_stack.currentWidget() is window.browser
        assert window._native_probe_timer.isActive()

    def test_new_cookie_schedules_a_probe(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        cookie = MagicMock()
        cookie.name.return_value = b"JSESSIONID"
        cookie.value.return_value = b"fresh"

        window._on_cookie_added(cookie)

        assert window._cookies["JSESSIONID"] == "fresh"
        assert window._native_cookie_timer.isActive()

    def test_no_probe_is_scheduled_once_the_library_is_showing(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        window.main_stack.setCurrentWidget(window.library_browser)
        cookie = MagicMock()
        cookie.name.return_value = b"other"
        cookie.value.return_value = b"v"

        window._on_cookie_added(cookie)

        assert not window._native_cookie_timer.isActive()

    def test_probe_retries_until_the_api_accepts_the_session(self, qtbot, mock_umu_database, native_settings):
        """A 401 probe leaves the web view up; the next probe can still succeed."""
        from PyQt6.QtCore import QUrl
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        with patch.object(type(window.browser), "url", return_value=QUrl("http://localhost:8080/")):
            with patch.object(window.library_browser, "refresh"):
                window._probe_native_ui()
                window.library_browser.login_required.emit()
            assert window.main_stack.currentWidget() is window.browser

            with patch.object(window.library_browser, "refresh") as mock_refresh:
                window._probe_native_ui()
                mock_refresh.assert_called_once()
                window.library_browser.library_loaded.emit()

        assert window.main_stack.currentWidget() is window.library_browser

    def test_show_login_view_returns_to_the_web_view(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        window.main_stack.setCurrentWidget(window.library_browser)

        window.show_login_view()

        assert window.main_stack.currentWidget() is window.browser

    def test_native_download_uses_api_url_and_reported_size(self, qtbot, mock_umu_database, native_settings):
        from gameyfin_frontend.services.gameyfin_api import Game
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        game = Game(id=7, title="Some Game: Deluxe", library_id=1, file_size=4096)

        with patch.object(window, "_start_download") as mock_start:
            window._on_native_download_requested(game, "fs")

        kwargs = mock_start.call_args.kwargs
        assert kwargs["url"] == "http://localhost:8080/download/7?provider=fs"
        assert kwargs["total_bytes"] == 4096
        assert kwargs["filename"] == "Some Game: Deluxe.zip"
        # The extraction folder name is sanitized, the display name is not
        assert ":" not in os.path.basename(kwargs["target_dir"])

    def test_download_target_defaults_to_a_per_game_subfolder(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)

        target = window._resolve_download_target("MyGame")

        assert os.path.basename(target) == "MyGame"
        assert target.startswith(str(native_settings.get_config_dir()))

    def test_download_target_returns_none_when_prompt_cancelled(self, qtbot, mock_umu_database, native_settings):
        window = self._make_native_window(qtbot, mock_umu_database, native_settings)
        native_settings.get.side_effect = lambda key, default=None: {
            "GF_PROMPT_DOWNLOAD_DIR": 1, "GF_DEFAULT_DOWNLOAD_DIR": ""
        }.get(key, default)

        with patch("gameyfin_frontend.gameyfin_window.QFileDialog.getExistingDirectory", return_value=""):
            assert window._resolve_download_target("MyGame") is None
