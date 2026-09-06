from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_umu_database():
    db = MagicMock()
    db.search_by_partial_title.return_value = []
    db.get_game_by_codename.return_value = []
    return db


class TestDownloadItemWidget:
    def test_widget_initializes_with_record(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Completed"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        assert "game" in widget.filename_label.text()

    def test_widget_shows_install_button_for_completed(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Completed"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        # Use isHidden() instead of isVisible() since parent window may not exist in tests
        assert not widget._install_group.isHidden()
        assert not widget.open_folder_button.isHidden()
        assert not widget.remove_button.isHidden()
        assert widget.cancel_button.isHidden()

    def test_widget_shows_remove_only_for_failed(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Failed"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        assert not widget.remove_button.isHidden()
        assert widget.cancel_button.isHidden()
        assert widget._install_group.isHidden()
        assert widget.open_folder_button.isHidden()

    def test_widget_shows_remove_only_for_cancelled(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Cancelled"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        assert not widget.remove_button.isHidden()
        assert widget.cancel_button.isHidden()
        assert widget._install_group.isHidden()

    def test_cancel_button_keeps_its_natural_width(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Downloading"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        widget.resize(900, 60)
        # The downloading state: Cancel alone in a column sized for the three
        # completed-state buttons, which it must not stretch across.
        widget.cancel_button.show()
        widget._install_group.hide()
        widget.open_folder_button.hide()
        widget.remove_button.hide()
        widget.show()
        qtbot.waitExposed(widget)

        assert widget.cancel_button.width() == widget.cancel_button.sizeHint().width()
        # ...and it stays flush with the right edge, where Remove sits.
        assert (widget.cancel_button.geometry().right()
                == widget.button_container.rect().right())

    def test_button_column_is_the_same_width_in_every_state(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget

        widths = []
        for status in ("Downloading", "Completed", "Failed"):
            record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": status}
            widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
            qtbot.addWidget(widget)
            widths.append(widget.button_container.width())

        assert len(set(widths)) == 1

    def test_download_item_in_list(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Completed"}
        manager = DownloadManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(manager)
        controller = DownloadItemWidget(umu_database=mock_umu_database, record=record, settings=manager.settings)
        manager.add_download_to_list(controller)
        assert manager.list_widget.count() == 1
        item = manager.list_widget.item(0)
        widget = manager.list_widget.itemWidget(item)
        assert widget is controller


class TestDownloadManagerWidget:
    def test_widget_initializes(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        widget = DownloadManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        assert widget.scroll_area is not None

    def test_load_history_empty(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        widget = DownloadManagerWidget(umu_database=mock_umu_database)
        # Override json_path to a non-existent file and re-load
        widget.json_path = "/nonexistent/downloads.json"
        widget.download_records = []  # Reset after __init__ already called load_history()
        widget.load_history()
        qtbot.addWidget(widget)
        assert widget.download_records == []

    def test_find_controller_by_url_not_found(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        widget = DownloadManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        result = widget.find_controller_by_url("http://example.com/file.zip")
        assert result is None


class TestPrefixManagerWidget:
    def test_widget_initializes(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
        widget = PrefixManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        assert widget.list_widget is not None
        assert widget.refresh_btn is not None

    def test_no_row_selection(self, qtbot, mock_umu_database):
        from PyQt6.QtWidgets import QAbstractItemView
        from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
        widget = PrefixManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        assert widget.list_widget.selectionMode() == QAbstractItemView.SelectionMode.NoSelection

    def test_refresh_prefixes_creates_dir(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
        widget = PrefixManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        # refresh_prefixes should create the prefixes dir if it doesn't exist
        widget.refresh_prefixes()
        assert widget.list_widget is not None


class TestPrefixItemWidget:
    def test_widget_initializes(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.prefix_manager import PrefixItemWidget
        widget = PrefixItemWidget("test_game_pfx", "/tmp/test_game_pfx", umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        assert "test_game_pfx" in widget.name_label.text()

    def test_script_combo_disabled_when_no_scripts(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.prefix_manager import PrefixItemWidget
        widget = PrefixItemWidget("empty_pfx", "/tmp/empty_pfx", umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        # Should show "No scripts found" and be disabled
        assert widget.script_combo.count() == 1
        assert widget.script_combo.itemText(0) == "No scripts found"


class TestGamepadHintBar:
    @staticmethod
    def _bar(qtbot, accent):
        from PyQt6.QtGui import QColor, QPalette

        from gameyfin_frontend.widgets.gamepad_hud import GamepadHintBar

        bar = GamepadHintBar()
        palette = bar.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor(accent))
        bar.setPalette(palette)
        qtbot.addWidget(bar)
        bar.set_hints([("A", "Select"), ("B", "Back")])
        return bar

    def test_badges_use_the_theme_accent(self, qtbot):
        bar = self._bar(qtbot, "#ff9800")

        for _chip, badge, _label, _action in bar._chips:
            assert "#ff9800" in badge.styleSheet()

    def test_badges_follow_a_theme_change(self, qtbot):
        from PyQt6.QtGui import QColor, QPalette

        bar = self._bar(qtbot, "#ff9800")
        palette = bar.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#8bc34a"))
        bar.setPalette(palette)

        bar.refresh_theme_colors()

        for _chip, badge, _label, _action in bar._chips:
            assert "#8bc34a" in badge.styleSheet()

    def test_badge_text_stays_readable_on_a_light_accent(self, qtbot):
        light_bar = self._bar(qtbot, "#ffffff")
        dark_bar = self._bar(qtbot, "#101010")

        assert "color: #000000" in light_bar._chips[0][1].styleSheet()
        assert "color: #ffffff" in dark_bar._chips[0][1].styleSheet()
