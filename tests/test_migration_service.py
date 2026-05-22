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
        assert result == {"settings": 0, "shortcuts": 0, "prefixes": 0}

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


class TestMigrationServicePrefixes:
    """Test prefix directory migration from legacy → new."""

    def _setup_legacy_prefixes(self, tmp_path, names=None):
        """Create legacy prefix directories."""
        if names is None:
            names = ["dark-earth_pfx", "lotro_pfx"]
        legacy_base = tmp_path / ".config" / "gameyfin" / "prefixes"
        legacy_base.mkdir(parents=True)
        for name in names:
            pfx_dir = legacy_base / name
            pfx_dir.mkdir()
            (pfx_dir / "drive_c").mkdir()
        return legacy_base

    def test_migrates_prefixes_not_in_new_location(self, tmp_path):
        """Prefixes are copied to new location if not already there."""
        self._setup_legacy_prefixes(tmp_path)

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["prefixes"] == 2
        new_base = tmp_path / "prefixes"
        assert (new_base / "dark-earth_pfx").exists()
        assert (new_base / "lotro_pfx").exists()

    def test_skips_existing_prefix_in_new_location(self, tmp_path):
        """Existing prefix in new location prevents migration."""
        self._setup_legacy_prefixes(tmp_path, names=["a_pfx"])
        # Also create it in the new location
        (tmp_path / "prefixes" / "a_pfx").mkdir(parents=True)

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["prefixes"] == 0

    def test_partial_migration_some_exist_already(self, tmp_path):
        """Only non-existing prefixes are migrated."""
        self._setup_legacy_prefixes(tmp_path, names=["existing_pfx", "new_pfx"])
        (tmp_path / "prefixes" / "existing_pfx").mkdir(parents=True)

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result["prefixes"] == 1


class TestMigrationServiceCombined:
    """Test that all three categories migrate together correctly."""

    def test_all_categories_migrated_together(self, tmp_path):
        """A full legacy setup migrates settings, shortcuts, and prefixes."""
        # Settings
        legacy_settings = tmp_path / ".config" / "gameyfin" / "settings.json"
        legacy_settings.parent.mkdir(parents=True)
        legacy_settings.write_text('{"GF_URL": "http://legacy.example.com"}')

        # Shortcuts
        legacy_shortcut = tmp_path / ".config" / "gameyfin" / "shortcut_scripts" / "my-game"
        legacy_shortcut.mkdir(parents=True)
        (legacy_shortcut / "my-game.sh").write_text("#!/bin/sh\numu-run x")

        # Prefixes
        legacy_prefix = tmp_path / ".config" / "gameyfin" / "prefixes" / "my-game_pfx"
        legacy_prefix.mkdir(parents=True)
        (legacy_prefix / "drive_c").mkdir()

        service = MigrationService(str(tmp_path))
        result = service.migrate()

        assert result == {"settings": 1, "shortcuts": 1, "prefixes": 1}
        assert (tmp_path / "settings.json").exists()
        assert (tmp_path / "shortcut_scripts" / "my-game" / "my-game.sh").exists()
        assert (tmp_path / "prefixes" / "my-game_pfx" / "drive_c").exists()
