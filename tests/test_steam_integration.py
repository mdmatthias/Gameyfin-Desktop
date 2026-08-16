"""Tests for the SteamIntegrationService — binary VDF shortcuts.vdf management."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gameyfin_frontend.services.steam_integration import SteamIntegrationService
from gameyfin_frontend.utils import sanitize_name


@pytest.fixture()
def mock_settings(tmp_path):
    settings = MagicMock()
    settings.get.return_value = 0
    settings.get_config_dir.return_value = str(tmp_path / "gameyfin_config")
    return settings


class TestSteamIntegrationService:
    """Test the high-level SteamIntegrationService API."""

    def test_init_creates_service(self, mock_settings):
        svc = SteamIntegrationService(mock_settings)
        assert svc.settings is mock_settings

    def test_add_game_fails_without_vdf(self, mock_settings):
        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=None):
            svc = SteamIntegrationService(mock_settings)
            result = svc.add_game_to_steam(
                name="TestGame",
                exe="/tmp/test.sh",
            )
            assert result is False

    def test_add_game_success_writes_binary_vdf(self, tmp_path, mock_settings):
        # Create a minimal shortcuts.vdf structure
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        initial_data = {"shortcuts": {}}
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(initial_data, f)

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        exe_path = str(scripts_dir / "run.sh")
        Path(exe_path).write_text("#!/bin/sh\n")

        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=vdf_path):
            svc = SteamIntegrationService(mock_settings)
            result = svc.add_game_to_steam(
                name="run.sh",  # Service is called with script basename by shortcut_service
                exe=exe_path,
            )
            assert result is True

        # Verify the file was written correctly
        with open(vdf_path, "rb") as f:
            data = _vdf_lib.binary_load(f)

        wrapper_path = os.path.join(
            mock_settings.get_config_dir(), "steam_shortcut_scripts", f"{sanitize_name('run.sh')}.sh"
        )

        shortcuts = data["shortcuts"]
        assert len(shortcuts) == 1
        entry = list(shortcuts.values())[0]
        assert entry["AppName"] == "run.sh"
        assert entry["Exe"] == "/bin/sh"
        assert entry["StartDir"] == "/bin"
        assert entry["ShortcutPath"] == "/bin/sh"
        assert entry["LaunchOptions"] == f'"{wrapper_path}"'
        assert entry["IsHidden"] == 1
        assert entry["AllowOverlay"] == 1
        assert entry["FlatpakAppID"] == ""

        # The wrapper script itself execs flatpak; no LD_PRELOAD handling.
        wrapper_content = Path(wrapper_path).read_text()
        assert "run --command=sh org.gameyfin.Gameyfin-Desktop" in wrapper_content
        assert exe_path in wrapper_content
        assert os.access(wrapper_path, os.X_OK)

    def test_add_game_assigns_unique_neg_id(self, tmp_path, mock_settings):
        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        # Pre-populate with IDs -1 and -2
        existing_data = {
            "shortcuts": {
                "-1": {"appid": -1, "AppName": "Existing1"},
                "-2": {"appid": -2, "AppName": "Existing2"},
            }
        }
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(existing_data, f)

        exe_path = str(tmp_path / "x.sh")
        Path(exe_path).write_text("#!/bin/sh\n")

        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=vdf_path):
            svc = SteamIntegrationService(mock_settings)
            svc.add_game_to_steam(name="NewGame", exe=exe_path)

        with open(vdf_path, "rb") as f:
            data = _vdf_lib.binary_load(f)

        appids = [e["appid"] for e in data["shortcuts"].values()]
        assert -3 in appids  # Should get next free negative ID

    def test_remove_game_by_name(self, tmp_path, mock_settings):
        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        initial_data = {
            "shortcuts": {
                "-1": {"appid": -1, "AppName": "RemoveMe"},
                "-2": {"appid": -2, "AppName": "KeepThis"},
            }
        }
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(initial_data, f)

        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=vdf_path):
            svc = SteamIntegrationService(mock_settings)
            result = svc.remove_game_from_steam(name="RemoveMe")
            assert result is True

        with open(vdf_path, "rb") as f:
            data = _vdf_lib.binary_load(f)

        names = [e["AppName"] for e in data["shortcuts"].values()]
        assert "RemoveMe" not in names
        assert "KeepThis" in names

    def test_remove_nonexistent_game_returns_false(self, tmp_path, mock_settings):
        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        initial_data = {"shortcuts": {"-1": {"appid": -1, "AppName": "Other"}}}
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(initial_data, f)

        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=vdf_path):
            svc = SteamIntegrationService(mock_settings)
            result = svc.remove_game_from_steam(name="NoSuchGame")
            assert result is False

    def test_add_game_updates_existing_by_name(self, tmp_path, mock_settings):
        """Adding a game that already exists should update in-place, not duplicate."""
        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        existing_data = {
            "shortcuts": {
                "-1": {"appid": -1, "AppName": "Anno.sh", "Exe": '"old"', "StartDir": "/old"},
            }
        }
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(existing_data, f)

        new_dir = tmp_path / "new_dir"
        new_dir.mkdir()
        exe_path = str(new_dir / "path.sh")
        Path(exe_path).write_text("#!/bin/sh\n")

        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=vdf_path):
            svc = SteamIntegrationService(mock_settings)
            result = svc.add_game_to_steam(
                name="Anno.sh",
                exe=exe_path,
            )
            assert result is True

        with open(vdf_path, "rb") as f:
            data = _vdf_lib.binary_load(f)

        wrapper_path = os.path.join(
            mock_settings.get_config_dir(), "steam_shortcut_scripts", f"{sanitize_name('Anno.sh')}.sh"
        )

        shortcuts = data["shortcuts"]
        assert len(shortcuts) == 1  # No duplicate entry
        entry = list(shortcuts.values())[0]
        assert entry["appid"] == -1  # Same AppID preserved
        assert entry["Exe"] == "/bin/sh"
        assert entry["LaunchOptions"] == f'"{wrapper_path}"'

    def test_get_steam_names_returns_set(self, tmp_path, mock_settings):
        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        initial_data = {
            "shortcuts": {
                "-1": {"appid": -1, "AppName": "Anno.sh"},
                "-2": {"appid": -2, "AppName": "Lotro.sh"},
            }
        }
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(initial_data, f)

        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=vdf_path):
            svc = SteamIntegrationService(mock_settings)
            names = svc.get_steam_names()

        # get_steam_names strips the .sh suffix so it matches checkbox labels.
        assert names == {"Anno", "Lotro"}

    def test_get_steam_names_empty_when_no_vdf(self, mock_settings):
        with patch("gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf", return_value=None):
            svc = SteamIntegrationService(mock_settings)
            names = svc.get_steam_names()
        assert names == set()

    def test_get_steam_names_keeps_non_sh_appnames(self, tmp_path, mock_settings):
        """AppNames NOT ending with .sh are returned as-is (our write format)."""
        try:
            import vdf as _vdf_lib
        except ImportError:
            pytest.skip("vdf package not installed")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        vdf_path = str(config_dir / "shortcuts.vdf")

        initial_data = {
            "shortcuts": {
                "-1": {"appid": -1, "AppName": "Anno 1404"},
                "-2": {"appid": -2, "AppName": "Anno 1404 - Venice"},
            }
        }
        with open(vdf_path, "wb") as f:
            _vdf_lib.binary_dump(initial_data, f)

        with patch(
            "gameyfin_frontend.services.steam_integration._find_steam_shortcuts_vdf",
            return_value=vdf_path,
        ):
            svc = SteamIntegrationService(mock_settings)
            names = svc.get_steam_names()

        assert names == {"Anno 1404", "Anno 1404 - Venice"}
