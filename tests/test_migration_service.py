"""Tests for MigrationService — one-time legacy→new data migration."""

import json
import os
import shutil

import pytest

from gameyfin_frontend.services import MigrationService


@pytest.fixture(autouse=True)
def _isolated_legacy_dir(tmp_path):
    """Ensure each test uses an isolated temp dir for the legacy config path."""
    legacy = str(tmp_path / ".config" / "gameyfin")
    original = MigrationService.LEGACY_CONFIG_DIR
    MigrationService.LEGACY_CONFIG_DIR = legacy
    yield
    MigrationService.LEGACY_CONFIG_DIR = original
    # Clean up after test in case copytree left partial dirs behind
    try:
        shutil.rmtree(legacy)
    except OSError:
        pass


class TestMigrationServiceSettings:
    """Test settings.json migration from legacy → new location."""

    def test_no_legacy_dir_returns_zero(self, tmp_path):
        """If no legacy directory exists, nothing is migrated."""
        service = MigrationService(str(tmp_path))
        result = service.migrate()
        assert result == {"settings": 0, "shortcuts": 0}

    def test_migrates_settings_when_new_does_not_exist(self, tmp_path):
        """Settings are copied from legacy → new when new doesn't exist yet."""
        legacy_dir = tmp_path / ".config" / "gameyfin"
        legacy_dir.mkdir(parents=True)
        legacy_settings = legacy_dir / "settings.json"
        legacy_settings.write_text(json.dumps({"GF_URL": "http://example.com"}))

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["settings"] == 1
        new_settings = tmp_path / "settings.json"
        assert new_settings.exists()
        assert json.loads(new_settings.read_text()) == {"GF_URL": "http://example.com"}

    def test_skips_settings_when_already_in_new_location(self, tmp_path):
        """Existing settings in new location prevent migration."""
        legacy_dir = tmp_path / ".config" / "gameyfin"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "settings.json").write_text('{"old": true}')
        (tmp_path / "settings.json").write_text('{"new": true}')

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["settings"] == 0

    def test_idempotent_on_second_call(self, tmp_path):
        """Calling migrate() twice returns empty results the second time."""
        legacy_dir = tmp_path / ".config" / "gameyfin"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "settings.json").write_text("{}")

        service = MigrationService(str(tmp_path))
        first = service.migrate()
        second = service.migrate()

        assert first["settings"] >= 0
        assert second == {}


class TestMigrationServiceShortcuts:
    """Test shortcut script directory migration from legacy → new."""

    def _setup_legacy_shortcuts(self, tmp_path, game_name="test-game"):
        """Create a legacy shortcut_scripts dir with some files."""
        legacy_base = tmp_path / ".config" / "gameyfin" / "shortcut_scripts"
        legacy_game = legacy_base / game_name
        legacy_game.mkdir(parents=True)
        (legacy_game / "config.json").write_text(json.dumps({"PROTONPATH": "GE-Proton9"}))
        sh_file = legacy_game / f"{game_name}.sh"
        sh_file.write_text("#!/bin/sh\numu-run /path/to/game.exe")
        return legacy_game

    def test_migrates_shortcut_dirs_when_not_in_new_location(self, tmp_path):
        """Shortcut dirs are copied if they don't exist in the new location."""
        self._setup_legacy_shortcuts(tmp_path)

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["shortcuts"] == 1
        new_dir = tmp_path / "shortcut_scripts" / "test-game"
        assert new_dir.exists()
        assert (new_dir / "config.json").exists()
        assert (new_dir / "test-game.sh").exists()

    def test_skips_shortcut_dir_already_in_new_location(self, tmp_path):
        """Existing shortcut dir in new location prevents migration."""
        legacy_game = self._setup_legacy_shortcuts(tmp_path)
        # Also create it in the new location with different content
        new_game = tmp_path / "shortcut_scripts" / "test-game"
        new_game.mkdir(parents=True)
        (new_game / "existing.txt").write_text("already here")

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["shortcuts"] == 0
        assert (new_game / "existing.txt").exists()

    def test_migrates_multiple_games_separately(self, tmp_path):
        """Multiple game dirs are each migrated independently."""
        for name in ["game-a", "game-b"]:
            base = tmp_path / ".config" / "gameyfin" / "shortcut_scripts" / name
            base.mkdir(parents=True)
            (base / f"{name}.sh").write_text("#!/bin/sh\numu-run x")

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["shortcuts"] == 2


class TestMigrationServiceCombined:
    """Test that settings and shortcuts migrate together correctly."""

    def test_settings_and_shortcuts_migrated_together(self, tmp_path):
        """A full legacy setup migrates settings and shortcut scripts."""
        # Settings
        legacy_settings = tmp_path / ".config" / "gameyfin" / "settings.json"
        legacy_settings.parent.mkdir(parents=True)
        legacy_settings.write_text('{"GF_URL": "http://legacy.example.com"}')

        # Shortcuts
        legacy_shortcut = tmp_path / ".config" / "gameyfin" / "shortcut_scripts" / "my-game"
        legacy_shortcut.mkdir(parents=True)
        (legacy_shortcut / "my-game.sh").write_text("#!/bin/sh\numu-run x")

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result == {"settings": 1, "shortcuts": 1}
        assert (tmp_path / "settings.json").exists()
        assert (tmp_path / "shortcut_scripts" / "my-game" / "my-game.sh").exists()
