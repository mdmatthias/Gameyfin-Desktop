"""Tests for the categorized layout of SettingsWidget."""
import sys

import pytest
from PyQt6.QtWidgets import QGroupBox, QScrollArea

from gameyfin_frontend.settings_widget import SettingsWidget


@pytest.fixture()
def settings_widget(qtbot, fresh_settings):
    widget = SettingsWidget(settings=fresh_settings)
    qtbot.addWidget(widget)
    return widget


def _section_by_title(widget, title):
    for box in widget.findChildren(QGroupBox):
        if box.title() == title:
            return box
    return None


class TestSettingsWidgetSections:
    def test_expected_sections_present(self, settings_widget):
        for title in ("General", "Library", "Downloads", "Gamepad"):
            assert _section_by_title(settings_widget, title) is not None, title

    def test_umu_section_only_on_linux(self, settings_widget):
        box = _section_by_title(settings_widget, "UMU")
        if sys.platform == "linux":
            assert box is not None
        else:
            # The widgets still exist for save_settings, but the section is not shown.
            assert box is None
            assert isinstance(settings_widget.stores_edit, object)

    def test_general_section_contains_connection_and_appearance(self, settings_widget):
        general = _section_by_title(settings_widget, "General")
        for control in (
            settings_widget.url_edit,
            settings_widget.width_spin,
            settings_widget.height_spin,
            settings_widget.minimized_check,
            settings_widget.theme_combo,
            settings_widget.log_level_combo,
            settings_widget.icon_path_edit,
        ):
            assert general.isAncestorOf(control)

    def test_library_section_contains_native_ui_controls(self, settings_widget):
        library = _section_by_title(settings_widget, "Library")
        assert library.isAncestorOf(settings_widget.native_ui_check)
        assert library.isAncestorOf(settings_widget.page_size_spin)

    def test_downloads_section_contains_download_controls(self, settings_widget):
        downloads = _section_by_title(settings_widget, "Downloads")
        for control in (
            settings_widget.download_dir_edit,
            settings_widget.prompt_download_check,
            settings_widget.notifications_check,
            settings_widget.bandwidth_slider,
        ):
            assert downloads.isAncestorOf(control)

    def test_gamepad_section_contains_gamepad_controls(self, settings_widget):
        gamepad = _section_by_title(settings_widget, "Gamepad")
        for control in (
            settings_widget.gamepad_enabled_check,
            settings_widget.gamepad_status_label,
            settings_widget.gamepad_hints_check,
            settings_widget.gamepad_deadzone_slider,
            settings_widget.gamepad_repeat_spin,
            settings_widget.gamepad_scroll_spin,
        ):
            assert gamepad.isAncestorOf(control)

    def test_sections_are_scrolled(self, settings_widget):
        scroll = settings_widget.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.widgetResizable()
        for box in settings_widget.findChildren(QGroupBox):
            assert scroll.isAncestorOf(box)

    def test_action_buttons_are_pinned_below_scroll(self, settings_widget):
        scroll = settings_widget.findChild(QScrollArea)
        assert not scroll.isAncestorOf(settings_widget.save_button)
        assert settings_widget.save_button.text() == "Save and Apply"

    def test_set_gamepad_status_updates_label(self, settings_widget):
        settings_widget.set_gamepad_status("Xbox Controller")
        assert settings_widget.gamepad_status_label.text() == "Xbox Controller"
