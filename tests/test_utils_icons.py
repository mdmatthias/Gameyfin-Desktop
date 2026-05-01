import os
import tempfile
from pathlib import Path

import pytest

from gameyfin_frontend.utils import copy_icon_from_source, install_icon_for_shortcut


class TestCopyIconFromSource:
    def test_finds_icon_in_icons_subdir(self, tmp_path):
        """Icon should be found in icons/<size>/apps/ subdirectory."""
        icon_dir = tmp_path / "proton_shortcuts" / "icons" / "256x256" / "apps"
        icon_dir.mkdir(parents=True)
        icon_file = icon_dir / "E97C_icon.png"
        icon_file.write_bytes(b"fake icon data")

        result = copy_icon_from_source(str(icon_dir.parent.parent.parent), "E97C_icon")
        assert result == str(icon_file)

    def test_finds_icon_by_name_without_extension(self, tmp_path):
        """Icon should be found by name even without .png extension."""
        icon_dir = tmp_path / "icons" / "128x128" / "apps"
        icon_dir.mkdir(parents=True)
        icon_file = icon_dir / "game_icon"
        icon_file.write_bytes(b"fake icon data")

        result = copy_icon_from_source(str(tmp_path), "game_icon")
        assert result == str(icon_file)

    def test_prefers_larger_sizes(self, tmp_path):
        """Should prefer 256x256 over 128x128 when both exist."""
        small_dir = tmp_path / "icons" / "32x32" / "apps"
        small_dir.mkdir(parents=True)
        (small_dir / "icon.png").write_bytes(b"small")

        large_dir = tmp_path / "icons" / "256x256" / "apps"
        large_dir.mkdir(parents=True)
        (large_dir / "icon.png").write_bytes(b"large")

        result = copy_icon_from_source(str(tmp_path), "icon")
        assert result == str(large_dir / "icon.png")

    def test_searches_drive_c_icons(self, tmp_path):
        """Should search drive_c/icons/ as a fallback location."""
        icon_dir = tmp_path / "drive_c" / "icons" / "64x64" / "apps"
        icon_dir.mkdir(parents=True)
        (icon_dir / "game_icon.png").write_bytes(b"data")

        result = copy_icon_from_source(str(tmp_path), "game_icon")
        assert result == str(icon_dir / "game_icon.png")

    def test_searches_drive_c_proton_shortcuts_icons(self, tmp_path):
        """Should search drive_c/proton_shortcuts/icons/ as a fallback location."""
        icon_dir = tmp_path / "drive_c" / "proton_shortcuts" / "icons" / "48x48" / "apps"
        icon_dir.mkdir(parents=True)
        (icon_dir / "shortcut_icon.png").write_bytes(b"data")

        result = copy_icon_from_source(str(tmp_path), "shortcut_icon")
        assert result == str(icon_dir / "shortcut_icon.png")

    def test_searches_parent_drive_c_proton_shortcuts_icons(self, tmp_path):
        """Should search ../drive_c/proton_shortcuts/icons/ from proton_shortcuts dir."""
        icon_dir = tmp_path / "drive_c" / "proton_shortcuts" / "icons" / "32x32" / "apps"
        icon_dir.mkdir(parents=True)
        expected_file = icon_dir / "parent_icon.png"
        expected_file.write_bytes(b"data")

        # source_dir is proton_shortcuts, so ../drive_c/proton_shortcuts/icons/ resolves correctly
        source_dir = tmp_path / "proton_shortcuts"
        source_dir.mkdir()
        result = copy_icon_from_source(str(source_dir), "parent_icon")
        assert result is not None
        assert os.path.realpath(result) == os.path.realpath(str(expected_file))

    def test_returns_none_when_not_found(self, tmp_path):
        """Should return None when icon doesn't exist in any location."""
        result = copy_icon_from_source(str(tmp_path), "nonexistent_icon")
        assert result is None

    def test_fallback_search_without_size_dir(self, tmp_path):
        """Should fall back to searching without size directory."""
        apps_dir = tmp_path / "icons" / "apps"
        apps_dir.mkdir(parents=True)
        (apps_dir / "fallback_icon.png").write_bytes(b"data")

        result = copy_icon_from_source(str(tmp_path), "fallback_icon")
        assert result == str(apps_dir / "fallback_icon.png")


class TestInstallIconForShortcut:
    def test_installs_icon_to_system_directory(self, tmp_path):
        """Icon should be copied to ~/.local/share/icons/gameyfin/<size>/apps/."""
        home = tmp_path / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)

        try:
            # Create source icon
            source_dir = tmp_path / "source" / "icons" / "256x256" / "apps"
            source_dir.mkdir(parents=True)
            source_icon = source_dir / "game_icon.0.png"
            source_icon.write_bytes(b"icon data")

            result = install_icon_for_shortcut(str(source_icon), "game_icon")
            assert result is not None
            assert os.path.exists(result)
            assert "gameyfin" in result
            assert "256x256" in result
            assert result.endswith(".png")
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)

    def test_returns_none_for_nonexistent_source(self, tmp_path):
        """Should return None when source icon doesn't exist."""
        result = install_icon_for_shortcut("/nonexistent/icon.png", "game_icon")
        assert result is None

    def test_returns_none_for_empty_path(self):
        """Should return None for empty or None path."""
        result = install_icon_for_shortcut("", "game_icon")
        assert result is None

    def test_derives_size_from_source_path(self, tmp_path):
        """Should derive size directory (128x128) from source path."""
        home = tmp_path / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)

        try:
            source_dir = tmp_path / "source" / "icons" / "128x128" / "apps"
            source_dir.mkdir(parents=True)
            source_icon = source_dir / "my_icon.png"
            source_icon.write_bytes(b"data")

            result = install_icon_for_shortcut(str(source_icon), "my_icon")
            assert result is not None
            assert "128x128" in result
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)

    def test_defaults_to_256x256_when_no_size_in_path(self, tmp_path):
        """Should default to 256x256 when no size directory is found in path."""
        home = tmp_path / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)

        try:
            source_dir = tmp_path / "source" / "apps"
            source_dir.mkdir(parents=True)
            source_icon = source_dir / "orphan_icon.png"
            source_icon.write_bytes(b"data")

            result = install_icon_for_shortcut(str(source_icon), "orphan_icon")
            assert result is not None
            assert "256x256" in result
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)

    def test_sanitizes_filename(self, tmp_path):
        """Should sanitize slashes in icon filename."""
        home = tmp_path / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)

        try:
            source_dir = tmp_path / "source" / "256x256" / "apps"
            source_dir.mkdir(parents=True)
            # Filename with slashes (edge case) - create nested dirs
            nested_dir = source_dir / "path" / "to"
            nested_dir.mkdir(parents=True)
            source_icon = nested_dir / "icon.png"
            source_icon.write_bytes(b"data")

            result = install_icon_for_shortcut(str(source_icon), "icon")
            assert result is not None
            # The basename would be "icon.png", slashes already removed by os.path.basename
            assert "/" not in os.path.basename(result)
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
