import json
import os
from collections import defaultdict

import pytest
import requests

from gameyfin_frontend.umu_database import UmuDatabase


@pytest.fixture()
def mock_settings(monkeypatch, tmp_app_data):
    """Mock settings_manager to use temp directory for cache."""
    from gameyfin_frontend import settings as settings_module

    class MockSettings:
        def __init__(self):
            self._data = {
                "GF_UMU_API_URL": "http://test.umu.api/umu_api.php",
                "GF_UMU_DB_STORES": ["none", "steam"],
            }
            self.cache_path = os.path.join(tmp_app_data, "umu_cache.json")

        def get(self, key, fallback=None):
            return self._data.get(key, fallback)

        def get_umu_cache_path(self):
            return self.cache_path

    mock = MockSettings()
    monkeypatch.setattr(settings_module, "settings_manager", mock)
    return mock


@pytest.fixture()
def fresh_umu_database(mock_settings):
    """Create a UmuDatabase with an empty cache (no API calls)."""
    # Patch _request_umu_api to return empty list (no network needed)
    db = UmuDatabase.__new__(UmuDatabase)
    db._games_by_title = defaultdict(list)
    db.cache_file_path = mock_settings.cache_path
    db._ROMAN_REPLACEMENTS = (
        (r'\bX\b', ' 10 '),
        (r'\bIX\b', ' 9 '),
        (r'\bVIII\b', ' 8 '),
        (r'\bVII\b', ' 7 '),
        (r'\bVI\b', ' 6 '),
        (r'\bIV\b', ' 4 '),
        (r'\bV\b', ' 5 '),
        (r'\bIII\b', ' 3 '),
        (r'\bII\b', ' 2 '),
        (r'\bI\b', ' 1 ')
    )
    return db


class TestNormalizeString:
    def test_removes_spaces(self, fresh_umu_database):
        assert fresh_umu_database._normalize_string("hello world") == "helloworld"

    def test_removes_punctuation(self, fresh_umu_database):
        assert fresh_umu_database._normalize_string("Baldur's Gate") == "baldursgate"

    def test_lowercase(self, fresh_umu_database):
        assert fresh_umu_database._normalize_string("HELLO") == "hello"

    def test_roman_numerals(self, fresh_umu_database):
        # II -> 2
        result = fresh_umu_database._normalize_string("Divinity II")
        assert "2" in result
        assert "ii" not in result

    def test_roman_numeral_x(self, fresh_umu_database):
        # X as standalone word (not part of XCOM) should be replaced
        result = fresh_umu_database._normalize_string("Game X")
        assert "10" in result

    def test_empty_string(self, fresh_umu_database):
        assert fresh_umu_database._normalize_string("") == ""

    def test_special_characters(self, fresh_umu_database):
        assert fresh_umu_database._normalize_string("!@#$%^&*()") == ""


class TestSearchByPartialTitle:
    def test_exact_match(self, fresh_umu_database, sample_umu_entries):
        fresh_umu_database._games_by_title = defaultdict(list, {e["title"]: [e] for e in sample_umu_entries})
        results = fresh_umu_database.search_by_partial_title("Baldur's Gate II")
        assert len(results) == 1
        assert results[0]["umu_id"] == "UMU-001"

    def test_partial_match(self, fresh_umu_database, sample_umu_entries):
        fresh_umu_database._games_by_title = defaultdict(list, {e["title"]: [e] for e in sample_umu_entries})
        results = fresh_umu_database.search_by_partial_title("baldurs")
        assert len(results) == 2  # Baldur's Gate II and III

    def test_case_insensitive(self, fresh_umu_database, sample_umu_entries):
        fresh_umu_database._games_by_title = defaultdict(list, {e["title"]: [e] for e in sample_umu_entries})
        results = fresh_umu_database.search_by_partial_title("WITCHER")
        assert len(results) == 1
        assert results[0]["umu_id"] == "UMU-003"

    def test_no_match(self, fresh_umu_database, sample_umu_entries):
        fresh_umu_database._games_by_title = defaultdict(list, {e["title"]: [e] for e in sample_umu_entries})
        results = fresh_umu_database.search_by_partial_title("NonexistentGame")
        assert len(results) == 0

    def test_empty_search(self, fresh_umu_database):
        results = fresh_umu_database.search_by_partial_title("")
        assert len(results) == 0

    def test_only_special_chars(self, fresh_umu_database):
        results = fresh_umu_database.search_by_partial_title("!@#$")
        assert len(results) == 0

    def test_roman_numeral_search(self, fresh_umu_database, sample_umu_entries):
        """Searching with Arabic numeral should match Roman numeral in title."""
        entries = [
            {"umu_id": "UMU-200", "title": "Divinity II - Throne of Autumn", "store": "gog"},
        ]
        fresh_umu_database._games_by_title = defaultdict(list, {e["title"]: [e] for e in entries})
        # "Divinity 2" -> "divinity2", "Divinity II - Throne of Autumn" -> "divinity2thronofautumn"
        results = fresh_umu_database.search_by_partial_title("Divinity 2")
        assert len(results) == 1

    def test_multiple_entries_same_title(self, fresh_umu_database):
        """Multiple entries with same title should all be returned."""
        entries = [
            {"umu_id": "UMU-100", "title": "Test Game", "store": "steam"},
            {"umu_id": "UMU-101", "title": "Test Game", "store": "gog"},
        ]
        fresh_umu_database._games_by_title = defaultdict(list, {"Test Game": entries})
        results = fresh_umu_database.search_by_partial_title("test")
        assert len(results) == 2


class TestBuildTitleCache:
    def test_builds_title_index(self, fresh_umu_database, sample_umu_entries):
        fresh_umu_database._build_title_cache(sample_umu_entries)
        assert "Baldur's Gate II" in fresh_umu_database._games_by_title
        assert len(fresh_umu_database._games_by_title["Baldur's Gate II"]) == 1

    def test_clears_existing_cache(self, fresh_umu_database):
        fresh_umu_database._games_by_title["Old Game"] = [{"umu_id": "OLD"}]
        fresh_umu_database._build_title_cache([])
        assert len(fresh_umu_database._games_by_title) == 0

    def test_handles_non_list_input(self, fresh_umu_database, capsys):
        fresh_umu_database._build_title_cache("not a list")
        assert len(fresh_umu_database._games_by_title) == 0
        captured = capsys.readouterr()
        assert "Error" in captured.out


class TestCachePersistence:
    def test_save_and_load_cache(self, fresh_umu_database, umu_cache_file, sample_umu_entries):
        fresh_umu_database._games_by_title = defaultdict(list, {e["title"]: [e] for e in sample_umu_entries})
        fresh_umu_database._save_cache_to_disk()

        assert os.path.exists(umu_cache_file)
        with open(umu_cache_file, "r") as f:
            loaded = json.load(f)
        assert "Baldur's Gate II" in loaded

    def test_load_cache_from_disk(self, fresh_umu_database, umu_cache_file, sample_umu_entries):
        cache_data = {e["title"]: [e] for e in sample_umu_entries}
        with open(umu_cache_file, "w") as f:
            json.dump(cache_data, f)

        fresh_umu_database._load_cache_from_disk()
        assert "Baldur's Gate II" in fresh_umu_database._games_by_title

    def test_load_corrupt_cache(self, fresh_umu_database, umu_cache_file, capsys):
        with open(umu_cache_file, "w") as f:
            f.write("{invalid json}")

        fresh_umu_database._load_cache_from_disk()
        captured = capsys.readouterr()
        assert "Failed to load cache" in captured.out


class TestUmuApiMethods:
    def test_list_all_calls_api(self, fresh_umu_database, mock_settings, monkeypatch):
        expected = [{"umu_id": "UMU-1", "title": "Test"}]
        monkeypatch.setattr(requests, "get", lambda *a, **kw: _mock_response(expected))
        result = fresh_umu_database.list_all()
        assert result == expected

    def test_list_all_by_store_calls_api_with_params(self, fresh_umu_database, monkeypatch):
        params_received = {}

        def capture_get(url, params=None, **kw):
            params_received.update(params or {})
            return _mock_response([])

        monkeypatch.setattr(requests, "get", capture_get)
        fresh_umu_database.list_all_by_store("steam")
        assert params_received.get("store") == "steam"

    def test_get_game_by_codename(self, fresh_umu_database, monkeypatch):
        params_received = {}

        def capture_get(url, params=None, **kw):
            params_received.update(params or {})
            return _mock_response([])

        monkeypatch.setattr(requests, "get", capture_get)
        fresh_umu_database.get_game_by_codename("UMU-123")
        assert params_received.get("codename") == "umu-123"

    def test_request_failure_returns_empty(self, fresh_umu_database, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
        result = fresh_umu_database.list_all()
        assert result == {}


def _mock_response(data):
    """Create a mock requests.Response."""
    response = requests.Response()
    response._content = json.dumps(data).encode()
    response.status_code = 200
    response.headers["Content-Type"] = "application/json"
    return response
