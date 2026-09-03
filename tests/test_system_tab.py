"""Tests for the System tab widget (system info + exit button)."""

import platform
from unittest.mock import patch

from PyQt6.QtWidgets import QLabel, QMessageBox

from gameyfin_frontend.config import APP_VERSION
from gameyfin_frontend.widgets.system_tab import SystemTabWidget


def _label_texts(widget):
    """Collect the text of every label in the widget tree."""
    return [label.text() for label in widget.findChildren(QLabel)]


class TestSystemTabWidget:
    def test_shows_version(self, qtbot, fresh_settings):
        tab = SystemTabWidget(settings=fresh_settings)
        qtbot.addWidget(tab)
        assert APP_VERSION in _label_texts(tab)

    def test_shows_platform_and_python(self, qtbot, fresh_settings):
        tab = SystemTabWidget(settings=fresh_settings)
        qtbot.addWidget(tab)
        texts = _label_texts(tab)
        assert platform.platform() in texts
        assert platform.python_version() in texts

    def test_shows_data_directory(self, qtbot, fresh_settings):
        tab = SystemTabWidget(settings=fresh_settings)
        qtbot.addWidget(tab)
        assert fresh_settings.get_config_dir() in _label_texts(tab)

    def test_works_without_settings(self, qtbot):
        tab = SystemTabWidget()
        qtbot.addWidget(tab)
        assert APP_VERSION in _label_texts(tab)

    def test_exit_button_emits_quit_requested(self, qtbot, fresh_settings):
        tab = SystemTabWidget(settings=fresh_settings)
        qtbot.addWidget(tab)
        emitted = []
        tab.quit_requested.connect(lambda: emitted.append(1))
        with patch("gameyfin_frontend.widgets.system_tab.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            tab.exit_button.click()
        assert emitted == [1]

    def test_exit_button_cancels_when_no(self, qtbot, fresh_settings):
        tab = SystemTabWidget(settings=fresh_settings)
        qtbot.addWidget(tab)
        emitted = []
        tab.quit_requested.connect(lambda: emitted.append(1))
        with patch("gameyfin_frontend.widgets.system_tab.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No):
            tab.exit_button.click()
        assert emitted == []

    def test_update_button_opens_update_dialog(self, qtbot, fresh_settings):
        tab = SystemTabWidget(settings=fresh_settings)
        qtbot.addWidget(tab)
        with patch("gameyfin_frontend.widgets.system_tab.UpdateDialog") as mock_dialog_cls:
            tab.update_button.click()
        mock_dialog_cls.assert_called_once_with(tab, fresh_settings)
        mock_dialog_cls.return_value.exec.assert_called_once()
