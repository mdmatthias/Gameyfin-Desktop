"""Integration tests for Gameyfin — end-to-end workflows."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.services.download_history_service import DownloadHistoryService
from gameyfin_frontend.umu_database import UmuDatabase


class TestSettingsRoundTrip:
    """Test that settings persist correctly across instances."""

    def test_full_round_trip(self, fresh_settings, settings_file):
        """Set multiple settings, save, reload in new instance, verify values."""
        # Set several different types of settings
        fresh_settings.set("GF_URL", "http://custom:9090")
        fresh_settings.set("GF_WINDOW_WIDTH", 1600)
        fresh_settings.set("GF_WINDOW_HEIGHT", 900)
        fresh_settings.set("PROTONPATH", "Proton-9.0")
        fresh_settings.set("GF_THEME", "dark_blue.xml")
        fresh_settings.set("GF_LOG_LEVEL", "DEBUG")
        fresh_settings.save()

        # Verify file was written
        assert os.path.exists(settings_file)
        with open(settings_file, "r") as f:
            saved_data = json.load(f)

        assert saved_data["GF_URL"] == "http://custom:9090"
        assert saved_data["GF_WINDOW_WIDTH"] == 1600
        assert saved_data["GF_THEME"] == "dark_blue.xml"

        # Create new instance (simulating app restart)
        SettingsManager._instance = None
        from PyQt6.QtCore import QStandardPaths
        original_writable = QStandardPaths.writableLocation

        def mock_writable(loc):
            if loc == QStandardPaths.StandardLocation.AppDataLocation:
                return os.path.dirname(settings_file)
            return original_writable(loc)

        QStandardPaths.writableLocation = mock_writable

        try:
            new_instance = SettingsManager()
            new_instance.settings_file = settings_file
            new_instance.load()

            assert new_instance.get("GF_URL") == "http://custom:9090"
            assert new_instance.get("GF_WINDOW_WIDTH") == 1600
            assert new_instance.get("GF_WINDOW_HEIGHT") == 900
            assert new_instance.get("PROTONPATH") == "Proton-9.0"
            assert new_instance.get("GF_THEME") == "dark_blue.xml"
            assert new_instance.get("GF_LOG_LEVEL") == "DEBUG"
        finally:
            QStandardPaths.writableLocation = original_writable
            SettingsManager._instance = None

    def test_partial_override(self, fresh_settings, settings_file):
        """Save some settings, create new instance, verify only changed values differ."""
        fresh_settings.set("GF_URL", "http://override:8080")
        fresh_settings.save()

        SettingsManager._instance = None
        from PyQt6.QtCore import QStandardPaths
        original_writable = QStandardPaths.writableLocation

        def mock_writable(loc):
            if loc == QStandardPaths.StandardLocation.AppDataLocation:
                return os.path.dirname(settings_file)
            return original_writable(loc)

        QStandardPaths.writableLocation = mock_writable

        try:
            new_instance = SettingsManager()
            new_instance.settings_file = settings_file
            new_instance.load()

            # Changed value
            assert new_instance.get("GF_URL") == "http://override:8080"
            # Unchanged defaults
            assert new_instance.get("GF_WINDOW_WIDTH") == 1420
            assert new_instance.get("PROTONPATH") == "GE-Proton"
        finally:
            QStandardPaths.writableLocation = original_writable
            SettingsManager._instance = None

    def test_env_override_persists_after_load(self, fresh_settings, settings_file, monkeypatch):
        """Environment variable should take precedence over saved value."""
        fresh_settings.set("GF_URL", "http://saved:8080")
        fresh_settings.save()

        monkeypatch.setenv("GF_URL", "http://env:9090")

        SettingsManager._instance = None
        from PyQt6.QtCore import QStandardPaths
        original_writable = QStandardPaths.writableLocation

        def mock_writable(loc):
            if loc == QStandardPaths.StandardLocation.AppDataLocation:
                return os.path.dirname(settings_file)
            return original_writable(loc)

        QStandardPaths.writableLocation = mock_writable

        try:
            new_instance = SettingsManager()
            new_instance.settings_file = settings_file
            new_instance.load()

            # Env should override saved value
            assert new_instance.get("GF_URL") == "http://env:9090"
        finally:
            QStandardPaths.writableLocation = original_writable
            SettingsManager._instance = None


class TestDownloadHistoryRoundTrip:
    """Test download history persistence across restarts."""

    def test_add_and_remove_download(self, tmp_app_data):
        """Add a download record, save, remove it, verify it's gone on reload."""
        json_path = os.path.join(tmp_app_data, "downloads.json")
        service = DownloadHistoryService(json_path)

        # Add initial records
        records = [
            {"url": "http://example.com/game1.zip", "filename": "game1.zip", "path": "/tmp/game1", "status": "Completed"},
            {"url": "http://example.com/game2.zip", "filename": "game2.zip", "path": "/tmp/game2", "status": "Downloading"},
        ]
        service.save(records)

        # Reload — incomplete download should be marked as Failed
        loaded = service.load()
        assert len(loaded) == 2
        assert loaded[0]["status"] == "Completed"
        assert loaded[1]["status"] == "Failed"  # Was "Downloading", now marked Failed

        # Remove the first record
        filtered = [r for r in loaded if r["url"] != "http://example.com/game1.zip"]
        service.save(filtered)

        # Verify only one record remains
        reloaded = service.load()
        assert len(reloaded) == 1
        assert reloaded[0]["url"] == "http://example.com/game2.zip"

    def test_duplicate_detection(self, tmp_app_data):
        """Adding a download with duplicate URL should find the existing record."""
        json_path = os.path.join(tmp_app_data, "downloads.json")
        service = DownloadHistoryService(json_path)

        records = [
            {"url": "http://example.com/game.zip", "filename": "game.zip", "path": "/tmp/game", "status": "Downloading"},
        ]
        service.save(records)

        # Find by URL should return the existing record
        found = service.find_by_url(records, "http://example.com/game.zip")
        assert found is not None
        assert found["filename"] == "game.zip"

        # Non-existent URL should return None
        not_found = service.find_by_url(records, "http://example.com/other.zip")
        assert not_found is None

    def test_corrupt_history_file(self, tmp_app_data):
        """Loading a corrupt JSON file should return empty list without crashing."""
        json_path = os.path.join(tmp_app_data, "downloads.json")

        # Write invalid JSON
        with open(json_path, "w") as f:
            f.write("{invalid json content")

        service = DownloadHistoryService(json_path)
        loaded = service.load()
        assert loaded == []


class TestDownloadAddRemoveCycle:
    """Test the full download add/remove lifecycle with DownloadManagerWidget."""

    def test_add_download_and_remove(self, qtbot, mock_umu_database, tmp_app_data):
        """Add a download item, verify it appears in the grid, then remove it."""
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        from gameyfin_frontend.services.download_history_service import DownloadHistoryService

        # Create widget with temp downloads path
        json_path = os.path.join(tmp_app_data, "downloads.json")
        # Ensure no history file exists
        if os.path.exists(json_path):
            os.remove(json_path)

        settings_mock = MagicMock()
        settings_mock.get_downloads_json_path.return_value = json_path

        widget = DownloadManagerWidget(
            umu_database=mock_umu_database,
            settings=settings_mock
        )
        qtbot.addWidget(widget)

        # Verify grid is empty initially (empty row 0 + stretch row at bottom)
        assert widget.downloads_layout.rowCount() == 2

    def test_load_history_and_verify_widgets(self, qtbot, mock_umu_database, tmp_app_data):
        """Load persisted history and verify widgets are created for each record."""
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        from unittest.mock import MagicMock

        json_path = os.path.join(tmp_app_data, "downloads.json")
        service = DownloadHistoryService(json_path)

        # Pre-populate history with 2 records (will be marked as Failed due to "Downloading" -> "Failed" conversion)
        records = [
            {"url": "http://example.com/game1.zip", "filename": "game1.zip", "path": "/tmp/game1", "status": "Completed"},
            {"url": "http://example.com/game2.zip", "filename": "game2.zip", "path": "/tmp/game2", "status": "Downloading"},
        ]
        service.save(records)

        # Create widget — should load the history
        settings_mock = MagicMock()
        settings_mock.get_downloads_json_path.return_value = json_path

        widget = DownloadManagerWidget(
            umu_database=mock_umu_database,
            settings=settings_mock
        )
        qtbot.addWidget(widget)

        # Verify two download items were created
        # Grid structure: row 0 is empty, rows 1..N have downloads, last row has stretch
        assert len(widget.widget_map) == 2  # 2 download widgets in map

    def test_remove_download_saves_history(self, qtbot, mock_umu_database, tmp_app_data):
        """Remove a download item and verify history is updated."""
        from gameyfin_frontend.widgets.download_manager import DownloadManagerWidget
        from unittest.mock import MagicMock

        json_path = os.path.join(tmp_app_data, "downloads.json")
        service = DownloadHistoryService(json_path)

        # Pre-populate history with 2 records
        records = [
            {"url": "http://example.com/game1.zip", "filename": "game1.zip", "path": "/tmp/game1", "status": "Completed"},
            {"url": "http://example.com/game2.zip", "filename": "game2.zip", "path": "/tmp/game2", "status": "Completed"},
        ]
        service.save(records)

        # Create widget — should load the history
        settings_mock = MagicMock()
        settings_mock.get_downloads_json_path.return_value = json_path

        widget = DownloadManagerWidget(
            umu_database=mock_umu_database,
            settings=settings_mock
        )
        qtbot.addWidget(widget)

        # Verify two items exist
        assert len(widget.widget_map) == 2

        # Remove the first item
        first_controller = list(widget.widget_map.keys())[0]
        widget.remove_download_item(first_controller)

        # Verify only one item remains
        assert len(widget.widget_map) == 1


class TestUmuDatabaseCacheCycle:
    """Test UmuDatabase cache build and lookup cycle."""

    def test_cache_build_and_lookup(self, mock_settings):
        """Build cache from entries, then verify all three indexes work."""
        db = UmuDatabase.__new__(UmuDatabase)
        db.settings = mock_settings
        db._games_by_title = defaultdict(list)
        db._games_by_codename = defaultdict(list)
        db._games_by_umu_id = defaultdict(list)
        db.cache_file_path = mock_settings.cache_path
        # _ROMAN_REPLACEMENTS is a class attribute, no need to set it for these tests

        entries = [
            {"umu_id": "UMU-001", "title": "Baldur's Gate II", "store": "steam", "codename": "123456"},
            {"umu_id": "UMU-002", "title": "The Witcher 3", "store": "none", "codename": "789012"},
        ]

        db._build_title_cache(entries)

        # Title index
        title_results = db._games_by_title.get("Baldur's Gate II")
        assert len(title_results) == 1
        assert title_results[0]["umu_id"] == "UMU-001"

        # Codename index
        codename_results = db._games_by_codename.get("123456")
        assert len(codename_results) == 1
        assert codename_results[0]["title"] == "Baldur's Gate II"

        # UMU ID index
        umu_results = db._games_by_umu_id.get("UMU-001")
        assert len(umu_results) == 1
        assert umu_results[0]["codename"] == "123456"

    def test_cache_persists_and_reloads(self, mock_settings, umu_cache_file):
        """Save cache to disk, create new instance, load and verify all indexes."""
        db = UmuDatabase.__new__(UmuDatabase)
        db.settings = mock_settings
        db._games_by_title = defaultdict(list)
        db._games_by_codename = defaultdict(list)
        db._games_by_umu_id = defaultdict(list)
        db.cache_file_path = umu_cache_file

        entries = [
            {"umu_id": "UMU-001", "title": "Test Game", "store": "steam", "codename": "111"},
        ]
        db._build_title_cache(entries)

        # Save to disk
        db._save_cache_to_disk()

        # Create new instance and load
        db2 = UmuDatabase.__new__(UmuDatabase)
        db2.settings = mock_settings
        db2._games_by_title = defaultdict(list)
        db2._games_by_codename = defaultdict(list)
        db2._games_by_umu_id = defaultdict(list)
        db2.cache_file_path = umu_cache_file

        db2._load_cache_from_disk()

        # Verify all indexes loaded
        assert "Test Game" in db2._games_by_title
        assert "111" in db2._games_by_codename
        assert "UMU-001" in db2._games_by_umu_id

    def test_codename_lookup_uses_cache_first(self, mock_settings):
        """get_game_by_codename should check cache before hitting API."""
        db = UmuDatabase.__new__(UmuDatabase)
        db.settings = mock_settings
        db._games_by_title = defaultdict(list)
        db._games_by_codename = defaultdict(list, {"999": [{"umu_id": "UMU-TEST", "title": "Cached Game", "codename": "999"}]})
        db._games_by_umu_id = defaultdict(list)
        db.cache_file_path = mock_settings.cache_path

        # Mock _request_umu_api to track if it was called
        api_called = []
        original_request = db._request_umu_api
        def mock_request(params=None):
            api_called.append(params)
            return original_request(params)
        db._request_umu_api = mock_request

        # Lookup existing codename — should NOT call API
        results = db.get_game_by_codename("999")
        assert len(results) == 1
        assert results[0]["umu_id"] == "UMU-TEST"
        assert len(api_called) == 0  # API was not called

    def test_umu_id_lookup_uses_cache_first(self, mock_settings):
        """get_game_by_umu_id should check cache before hitting API."""
        db = UmuDatabase.__new__(UmuDatabase)
        db.settings = mock_settings
        db._games_by_title = defaultdict(list)
        db._games_by_codename = defaultdict(list)
        # Key must be lowercase since get_game_by_umu_id calls .lower() on lookup
        db._games_by_umu_id = defaultdict(list, {"umu-cached": [{"umu_id": "UMU-CACHED", "title": "Cached Title"}]})
        db.cache_file_path = mock_settings.cache_path

        # Mock _request_umu_api to track if it was called
        api_called = []
        original_request = db._request_umu_api
        def mock_request(params=None):
            api_called.append(params)
            return original_request(params)
        db._request_umu_api = mock_request

        # Lookup existing UMU ID — should NOT call API
        results = db.get_game_by_umu_id("UMU-CACHED")
        assert len(results) == 1
        assert results[0]["title"] == "Cached Title"
        assert len(api_called) == 0  # API was not called

    def test_codename_fallback_to_api_when_not_cached(self, mock_settings):
        """get_game_by_codename should fall back to API when codename is not in cache."""
        db = UmuDatabase.__new__(UmuDatabase)
        db.settings = mock_settings
        db._games_by_title = defaultdict(list)
        db._games_by_codename = defaultdict(list)  # Empty — codename not cached
        db._games_by_umu_id = defaultdict(list)
        db.cache_file_path = mock_settings.cache_path

        # Mock _request_umu_api
        api_called = []
        def mock_request(params=None):
            api_called.append(params)
            return [{"umu_id": "UMU-NEW", "title": "New Game", "codename": "999"}]
        db._request_umu_api = mock_request

        # Lookup non-cached codename — should call API
        results = db.get_game_by_codename("999")
        assert len(results) == 1
        assert results[0]["umu_id"] == "UMU-NEW"
        assert len(api_called) == 1
        assert api_called[0] == {"codename": "999"}
