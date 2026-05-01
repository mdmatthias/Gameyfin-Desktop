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
        assert not widget.install_button.isHidden()
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
        assert widget.install_button.isHidden()
        assert widget.open_folder_button.isHidden()

    def test_widget_shows_remove_only_for_cancelled(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Cancelled"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        assert not widget.remove_button.isHidden()
        assert widget.cancel_button.isHidden()
        assert widget.install_button.isHidden()

    def test_get_widgets_for_grid(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget
        record = {"filename": "game.zip", "path": "/tmp/downloads/game", "status": "Completed"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record)
        qtbot.addWidget(widget)
        widgets = widget.get_widgets_for_grid()
        assert len(widgets) == 5
        assert isinstance(widgets[0], type(widget.icon_label))


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

    def test_config_button_disabled_initially(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
        widget = PrefixManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        assert widget.config_btn.isEnabled() is False
        assert widget.delete_btn.isEnabled() is False

    def test_refresh_prefixes_creates_dir(self, qtbot, mock_umu_database):
        from gameyfin_frontend.widgets.prefix_manager import PrefixManagerWidget
        widget = PrefixManagerWidget(umu_database=mock_umu_database)
        qtbot.addWidget(widget)
        # refresh_prefixes should create the prefixes dir if it doesn't exist
        widget.refresh_prefixes()
        assert widget.list_widget is not None


class TestPrefixItemWidget:
    def test_widget_initializes(self, qtbot):
        from gameyfin_frontend.widgets.prefix_manager import PrefixItemWidget
        widget = PrefixItemWidget("test_game_pfx", "/tmp/test_game_pfx")
        qtbot.addWidget(widget)
        assert "test_game_pfx" in widget.name_label.text()

    def test_script_combo_disabled_when_no_scripts(self, qtbot):
        from gameyfin_frontend.widgets.prefix_manager import PrefixItemWidget
        widget = PrefixItemWidget("empty_pfx", "/tmp/empty_pfx")
        qtbot.addWidget(widget)
        # Should show "No scripts found" and be disabled
        assert widget.script_combo.count() == 1
        assert widget.script_combo.itemText(0) == "No scripts found"
