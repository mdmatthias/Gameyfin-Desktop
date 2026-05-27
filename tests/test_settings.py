import json
import os

import pytest

from gameyfin_frontend.settings import SettingsManager


@pytest.fixture()
def fresh_settings(settings_file, tmp_app_data):
    """Return a fresh SettingsManager instance with no prior state."""
    # Force a new singleton by clearing the existing one
    SettingsManager._instance = None

    # Patch QStandardPaths to use our temp directory
    from PyQt6.QtCore import QStandardPaths
    original_writable = QStandardPaths.writableLocation

    def mock_writable(loc):
        if loc == QStandardPaths.StandardLocation.AppDataLocation:
            return tmp_app_data
        return original_writable(loc)

    QStandardPaths.writableLocation = mock_writable

    try:
        sm = SettingsManager()
        # Ensure settings file is the one we control
        sm.settings_file = settings_file
        if os.path.exists(settings_file):
            os.remove(settings_file)
        sm.settings = sm.defaults.copy()
        yield sm
    finally:
        QStandardPaths.writableLocation = original_writable
        SettingsManager._instance = None


class TestSettingsManagerDefaults:
    def test_has_gf_url_default(self, fresh_settings):
        assert fresh_settings.get("GF_URL") == "http://localhost:8080"

    def test_has_window_dimensions(self, fresh_settings):
        assert fresh_settings.get("GF_WINDOW_WIDTH") == 1420
        assert fresh_settings.get("GF_WINDOW_HEIGHT") == 940

    def test_has_protonpath_default(self, fresh_settings):
        assert fresh_settings.get("PROTONPATH") == "GE-Proton"

    def test_has_umu_api_url(self, fresh_settings):
        assert "umu.openwinecomponents.org" in fresh_settings.get("GF_UMU_API_URL")

    def test_has_theme_default(self, fresh_settings):
        assert fresh_settings.get("GF_THEME") == "auto"

    def test_has_stores_list(self, fresh_settings):
        stores = fresh_settings.get("GF_UMU_DB_STORES")
        assert isinstance(stores, list)
        assert "none" in stores
        assert "steam" in stores


class TestSettingsManagerGetSet:
    def test_set_and_get(self, fresh_settings):
        fresh_settings.set("TEST_KEY", "test_value")
        assert fresh_settings.get("TEST_KEY") == "test_value"

    def test_set_overrides_default(self, fresh_settings):
        assert fresh_settings.get("GF_URL") == "http://localhost:8080"
        fresh_settings.set("GF_URL", "http://custom:9090")
        assert fresh_settings.get("GF_URL") == "http://custom:9090"

    def test_get_with_fallback(self, fresh_settings):
        result = fresh_settings.get("NONEXISTENT_KEY", "fallback_val")
        assert result == "fallback_val"

    def test_get_returns_default_for_missing_key(self, fresh_settings):
        result = fresh_settings.get("GF_START_MINIMIZED")
        assert result == 0

    def test_set_int_value(self, fresh_settings):
        fresh_settings.set("GF_START_MINIMIZED", 1)
        assert fresh_settings.get("GF_START_MINIMIZED") == 1


class TestSettingsManagerEnvOverride:
    def test_env_var_overrides_setting(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("GF_URL", "http://env-var:1234")
        assert fresh_settings.get("GF_URL") == "http://env-var:1234"

    def test_env_var_int_conversion(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("GF_START_MINIMIZED", "1")
        assert fresh_settings.get("GF_START_MINIMIZED") == 1

    def test_env_var_non_int_kept_as_string(self, fresh_settings, monkeypatch):
        monkeypatch.setenv("GF_WINDOW_WIDTH", "not_a_number")
        # When int conversion fails, the env var value is returned as-is (string)
        assert fresh_settings.get("GF_WINDOW_WIDTH") == "not_a_number"


class TestSettingsManagerPersistence:
    def test_save_and_load(self, fresh_settings, settings_file):
        fresh_settings.set("CUSTOM_SETTING", "hello")
        fresh_settings.save()

        # Create a new instance and load
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
            assert new_instance.get("CUSTOM_SETTING") == "hello"
        finally:
            QStandardPaths.writableLocation = original_writable
            SettingsManager._instance = None

    def test_load_corrupt_json(self, fresh_settings, settings_file):
        with open(settings_file, "w") as f:
            f.write("{invalid json content")

        fresh_settings.load()
        # Should not crash, settings should remain at defaults
        assert fresh_settings.get("GF_URL") == "http://localhost:8080"


class TestSettingsManagerPaths:
    def test_get_config_dir(self, fresh_settings, tmp_app_data):
        assert fresh_settings.get_config_dir() == tmp_app_data

    def test_get_prefixes_dirs(self, fresh_settings, tmp_app_data):
        dirs = fresh_settings.get_prefixes_dirs()
        assert len(dirs) >= 1
        assert os.path.join(tmp_app_data, "prefixes") in dirs

    def test_get_shortcuts_dirs(self, fresh_settings, tmp_app_data):
        dirs = fresh_settings.get_shortcuts_dirs("MyGame")
        assert len(dirs) >= 1
        assert os.path.join(tmp_app_data, "shortcut_scripts", "MyGame") in dirs

    def test_get_downloads_json_path(self, fresh_settings, tmp_app_data):
        path = fresh_settings.get_downloads_json_path()
        assert path.endswith("downloads.json")
        assert tmp_app_data in path

    def test_get_umu_cache_path(self, fresh_settings, tmp_app_data):
        path = fresh_settings.get_umu_cache_path()
        assert path.endswith("umu_cache.json")
        assert tmp_app_data in path
