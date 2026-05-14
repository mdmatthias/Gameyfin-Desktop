"""Tests for the system tray (GameyfinTray)."""

from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QSystemTrayIcon


@pytest.fixture()
def tray_app(qtbot):
    """Return a real QApplication for tray tests."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def mock_window():
    """Return a mock GameyfinWindow."""
    window = MagicMock()
    window.isVisible.return_value = True
    window.is_really_quitting = False
    return window


@pytest.fixture()
def mock_settings():
    """Return a mock SettingsManager."""
    settings = MagicMock()
    settings.get.return_value = ""
    return settings


@pytest.fixture()
def tray(tray_app, mock_window, mock_settings):
    """Return a GameyfinTray instance."""
    from gameyfin_frontend.gameyfin_tray import GameyfinTray
    return GameyfinTray(tray_app, mock_window, mock_settings)


class TestGameyfinTray:
    def test_tray_initializes(self, tray):
        assert tray.tray is not None
        assert tray.tray.isVisible()

    def test_tray_has_menu(self, tray):
        assert tray.menu is not None
        assert tray.tray.contextMenu() is tray.menu

    def test_tray_menu_has_actions(self, tray):
        actions = tray.menu.actions()
        # 4 actions + 1 separator = 5 items
        assert len(actions) == 5
        assert actions[0].text() == "Gameyfin"
        assert actions[1].text() == "Downloads"
        assert actions[2].text() == "Settings"
        assert actions[4].text() == "Quit"

    def test_tray_has_tooltip(self, tray):
        assert tray.tray.toolTip() == "Gameyfin"

    def test_show_action_connects_to_show_main_tab(self, tray):
        tray.show_action.trigger()
        tray.window.show_main_tab.assert_called_once()

    def test_downloads_action_connects_to_show_downloads_tab(self, tray):
        tray.downloads_action.trigger()
        tray.window.show_downloads_tab.assert_called_once()

    def test_settings_action_connects_to_show_settings_tab(self, tray):
        tray.settings_action.trigger()
        tray.window.show_settings_tab.assert_called_once()

    def test_quit_action_connects_to_quit_app(self, tray, monkeypatch):
        monkeypatch.setattr(tray.app, 'exit', MagicMock())
        tray.quit_action.trigger()
        assert tray.window.is_really_quitting is True
        tray.window.close.assert_called_once()
        tray.app.exit.assert_called_once()

    def test_icon_clicked_trigger_hides_visible_window(self, tray):
        tray.window.isVisible.return_value = True
        tray.icon_clicked(QSystemTrayIcon.ActivationReason.Trigger)
        tray.window.hide.assert_called_once()

    def test_icon_clicked_trigger_shows_hidden_window(self, tray):
        tray.window.isVisible.return_value = False
        tray.icon_clicked(QSystemTrayIcon.ActivationReason.Trigger)
        tray.window.show_main_tab.assert_called_once()

    def test_icon_clicked_non_trigger_does_nothing(self, tray):
        tray.icon_clicked(QSystemTrayIcon.ActivationReason.Unknown)
        tray.window.hide.assert_not_called()
        tray.window.show_main_tab.assert_not_called()

    def test_quit_app_hides_tray(self, tray):
        tray.quit_app()
        assert not tray.tray.isVisible()
