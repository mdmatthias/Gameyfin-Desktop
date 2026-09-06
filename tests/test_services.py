"""Tests for the services package — LauncherResolver, GameInstaller, GameLauncher."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from gameyfin_frontend.services import LauncherResolver, GameInstaller, GameLauncher


@pytest.fixture()
def mock_umu_database():
    db = MagicMock()
    db.search_by_partial_title.return_value = []
    db.get_game_by_codename.return_value = []
    return db


@pytest.fixture()
def mock_settings():
    settings = MagicMock()
    settings.get_prefixes_dir.return_value = "/tmp/prefixes"
    settings.get.return_value = "GE-Proton"
    return settings


class TestLauncherResolver:
    def test_find_launcher_paths_single_exe(self, tmp_path, mock_umu_database, mock_settings):
        resolver = LauncherResolver()
        game_dir = tmp_path / "game"
        game_dir.mkdir()
        (game_dir / "game.exe").touch()

        paths = resolver.find_launcher_paths(str(game_dir))
        assert len(paths) == 1
        assert paths[0].endswith("game.exe")

    def test_find_launcher_paths_multiple_exes(self, tmp_path, mock_umu_database, mock_settings):
        resolver = LauncherResolver()
        game_dir = tmp_path / "game"
        game_dir.mkdir()
        (game_dir / "game.exe").touch()
        (game_dir / "launcher.exe").touch()
        (game_dir / "sub").mkdir()
        (game_dir / "sub" / "helper.exe").touch()

        paths = resolver.find_launcher_paths(str(game_dir))
        assert len(paths) == 3

    def test_find_launcher_paths_no_exe(self, tmp_path, mock_umu_database, mock_settings):
        resolver = LauncherResolver()
        game_dir = tmp_path / "game"
        game_dir.mkdir()
        (game_dir / "readme.txt").touch()

        paths = resolver.find_launcher_paths(str(game_dir))
        assert paths == []

    def test_handle_launcher_selection_single(self, tmp_path, mock_umu_database, mock_settings):
        resolver = LauncherResolver()
        game_dir = tmp_path / "game"
        game_dir.mkdir()
        (game_dir / "game.exe").touch()

        result = resolver.handle_launcher_selection(
            target_dir=str(game_dir),
            parent=None,
        )
        assert result is not None
        assert result.endswith("game.exe")

    def test_handle_launcher_selection_no_exe(self, tmp_path, mock_umu_database, mock_settings):
        resolver = LauncherResolver()
        game_dir = tmp_path / "game"
        game_dir.mkdir()

        result = resolver.handle_launcher_selection(
            target_dir=str(game_dir),
            parent=None,
        )
        assert result is None


class TestGameInstaller:
    def test_build_wine_prefix(self, mock_umu_database, mock_settings):
        from gameyfin_frontend.services import GameInstaller

        installer = GameInstaller(mock_umu_database, mock_settings, None)
        prefix = installer.build_wine_prefix("/tmp/downloads/My_Game")
        assert prefix == "/tmp/prefixes/my_game_pfx"

    def test_detect_umu_game_id_codename(self, tmp_path, mock_umu_database, mock_settings):
        from gameyfin_frontend.services import GameInstaller

        game_dir = tmp_path / "game"
        game_dir.mkdir()
        (game_dir / "product_12345.json").write_text(json.dumps({"id": "umu-12345"}))

        mock_umu_database.get_game_by_codename.return_value = [
            {"umu_id": "umu-12345", "store": "steam"}
        ]

        installer = GameInstaller(mock_umu_database, mock_settings, None)
        umu_id, store = installer.detect_umu_game_id(str(game_dir))

        assert umu_id == "umu-12345"
        assert store == "steam"

    def test_detect_umu_game_id_title_fallback(self, tmp_path, mock_umu_database, mock_settings):
        from gameyfin_frontend.services import GameInstaller

        game_dir = tmp_path / "game"
        game_dir.mkdir()
        # No product_*.json — should fall back to filename

        # Mock settings.get to return the filename and path for title-based search
        mock_settings.get.side_effect = lambda key, default=None: {
            "filename": "my_awesome_game.zip",
            "path": str(game_dir),
        }.get(key, default)

        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "umu-99999", "store": "gog"}
        ]

        installer = GameInstaller(mock_umu_database, mock_settings, None)
        umu_id, store = installer.detect_umu_game_id(str(game_dir))

        assert umu_id == "umu-99999"
        assert store == "gog"

    def test_detect_umu_game_id_no_results(self, tmp_path, mock_umu_database, mock_settings):
        from gameyfin_frontend.services import GameInstaller

        game_dir = tmp_path / "game"
        game_dir.mkdir()
        # No product json, no search results

        installer = GameInstaller(mock_umu_database, mock_settings, None)
        umu_id, store = installer.detect_umu_game_id(str(game_dir))

        assert umu_id == "umu-default"
        assert store == "none"

    def test_detect_umu_game_id_invalid_json(self, tmp_path, mock_umu_database, mock_settings):
        from gameyfin_frontend.services import GameInstaller

        game_dir = tmp_path / "game"
        game_dir.mkdir()
        (game_dir / "product_bad.json").write_text("not valid json {{{")

        installer = GameInstaller(mock_umu_database, mock_settings, None)
        umu_id, store = installer.detect_umu_game_id(str(game_dir))

        assert umu_id == "umu-default"
        assert store == "none"

    def test_prompt_install_config_cancelled(self, mock_umu_database, mock_settings):
        from gameyfin_frontend.services import GameInstaller

        mock_settings.get_prefixes_dir.return_value = "/tmp/prefixes"
        installer = GameInstaller(mock_umu_database, mock_settings, None)

        with patch("gameyfin_frontend.services.game_installer.InstallConfigDialog") as MockDialog:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = MagicMock()
            mock_dialog.exec.return_value.value = 1  # Rejected
            MockDialog.return_value = mock_dialog

            result = installer.prompt_install_config(
                umu_id="umu-123",
                store="steam",
                wine_prefix_path="/tmp/pfx",
            )
            assert result is None


class TestGameLauncher:
    @pytest.mark.skipif(sys.platform == "win32", reason="Linux-only test")
    def test_start_linux_builds_command(self, mock_settings):
        from gameyfin_frontend.services import GameLauncher

        launcher = GameLauncher()

        with patch("gameyfin_frontend.services.game_launcher.QProcess") as MockProcess:
            mock_process = MagicMock()
            mock_process.waitForStarted.return_value = True
            MockProcess.return_value = mock_process

            result = launcher.start_linux(
                launcher_to_run="/tmp/game/game.exe",
                target_dir="/tmp/game",
                install_config={"USE_HOST_UMU": "1"},
                wine_prefix_path="/tmp/prefixes/my_game_pfx",
                proton_path="GE-Proton10",
            )

            assert result is not None
            MockProcess.return_value.start.assert_called_once()
            call_args = MockProcess.return_value.start.call_args
            assert call_args[0][0] == "/bin/sh"
            # start("/bin/sh", ["-c", "PROTONPATH=... exec umu-run ..."])
            command_str = call_args[0][1][1]
            assert "exec umu-run" in command_str
            assert 'PROTONPATH="GE-Proton10"' in command_str

    @pytest.mark.skipif(sys.platform == "win32", reason="Linux-only test")
    def test_start_linux_missing_prefix(self, mock_settings):
        from gameyfin_frontend.services import GameLauncher

        launcher = GameLauncher()

        result = launcher.start_linux(
            launcher_to_run="/tmp/game/game.exe",
            target_dir="/tmp/game",
            install_config={},
            wine_prefix_path="",
        )
        assert result is None

    @pytest.mark.skipif(sys.platform == "win32", reason="Linux-only test")
    def test_start_windows(self):
        from gameyfin_frontend.services import GameLauncher

        launcher = GameLauncher()

        with patch("gameyfin_frontend.services.game_launcher.QProcess") as MockProcess:
            mock_process = MagicMock()
            mock_process.waitForStarted.return_value = True
            MockProcess.return_value = mock_process

            result = launcher.start_windows("/tmp/game/game.exe")

            assert result is not None
            MockProcess.return_value.setProgram.assert_called_once_with("/tmp/game/game.exe")
            MockProcess.return_value.setWorkingDirectory.assert_called_once_with("/tmp/game")
            MockProcess.return_value.start.assert_called_once()

class TestInstallConfigPersistence:
    """The config a game is installed with has to outlive the install run.

    Manage > Config and Manage > Shortcuts both start from config.json; when it
    is missing, the auto-detected GAMEID/STORE live only in the generated .sh
    scripts and the next rewrite silently falls back to "umu-default".
    """

    @pytest.mark.skipif(sys.platform == "win32", reason="Linux-only test")
    def test_install_writes_config_next_to_the_scripts(self, qtbot, tmp_path, mock_umu_database):
        from gameyfin_frontend.widgets.download_item import DownloadItemWidget

        settings = MagicMock()
        settings.get.return_value = "GE-Proton"
        settings.get_shortcuts_dir.return_value = str(tmp_path / "scripts" / "my game")

        record = {"filename": "my game.zip", "path": str(tmp_path / "my game"), "status": "Completed"}
        widget = DownloadItemWidget(umu_database=mock_umu_database, record=record, settings=settings)
        qtbot.addWidget(widget)
        widget.current_wine_prefix = str(tmp_path / "prefixes" / "my game_pfx")

        config = {"GAMEID": "umu-367500", "STORE": "steam", "MANGOHUD": "0"}
        with patch("gameyfin_frontend.services.game_launcher.QProcess") as MockProcess:
            MockProcess.return_value.waitForStarted.return_value = True
            widget._start_linux_installation("/tmp/game/game.exe", str(tmp_path / "my game"), config)

        settings.get_shortcuts_dir.assert_called_with("my game")
        with open(tmp_path / "scripts" / "my game" / "config.json") as f:
            assert json.load(f) == config

    def test_recreating_shortcuts_keeps_the_installed_env(self, tmp_path):
        """Without a config.json the env is read back out of the .sh scripts."""
        from gameyfin_frontend.services.shortcut_service import ShortcutService

        scripts_dir = tmp_path / "scripts" / "my game"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "My Game.sh").write_text(
            '#!/bin/sh\n\n'
            'PROTONPATH="GE-Proton" WINEPREFIX="/prefixes/my game_pfx" '
            'GAMEID="umu-367500" STORE="steam" umu-run "/prefixes/my game_pfx/game.exe"\n'
        )

        prefix = tmp_path / "prefixes" / "my game_pfx"
        desktop_dir = prefix / "drive_c" / "proton_shortcuts"
        desktop_dir.mkdir(parents=True)
        (desktop_dir / "My Game.desktop").write_text(
            "[Desktop Entry]\nName=My Game\nPath=/prefixes/my game_pfx\nStartupWMClass=game.exe\n"
        )

        settings = MagicMock()
        settings.get.return_value = "GE-Proton"
        settings.get_shortcuts_dirs.return_value = [str(scripts_dir)]
        settings.get_shortcuts_dir.return_value = str(scripts_dir)

        service = ShortcutService(settings)
        assert service.create_shortcuts_for_prefix(str(prefix), "my game", [], [], MagicMock())

        rebuilt = (scripts_dir / "My Game.sh").read_text()
        assert 'GAMEID="umu-367500"' in rebuilt
        assert 'STORE="steam"' in rebuilt
