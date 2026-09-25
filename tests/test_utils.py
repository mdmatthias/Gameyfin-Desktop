import os
import tempfile
from pathlib import Path

import pytest

from gameyfin_frontend.utils import (
    parse_desktop_file,
    build_umu_command,
    build_umu_env_prefix,
    build_flatpak_exec_command,
    get_xdg_user_dir,
    release_year,
    resolve_script_config,
    script_settings,
)


class TestParseDesktopFile:
    def test_parse_valid_desktop_file(self, valid_desktop_file):
        result = parse_desktop_file(valid_desktop_file)
        assert result is not None
        assert "Desktop Entry" in result
        assert result["Desktop Entry"]["Name"] == "TestGame"

    def test_parse_desktop_file_missing_header(self, desktop_file_missing_header):
        result = parse_desktop_file(desktop_file_missing_header)
        assert result is not None
        assert "Desktop Entry" in result
        assert result["Desktop Entry"]["Name"] == "TestGameNoHeader"

    def test_parse_invalid_file(self, invalid_desktop_file):
        result = parse_desktop_file(invalid_desktop_file)
        assert result is None

    def test_parse_nonexistent_file(self):
        result = parse_desktop_file("/nonexistent/path/file.desktop")
        assert result is None

    def test_preserves_case_of_keys(self, valid_desktop_file):
        """ConfigParser should preserve key case (optionxform = str)."""
        result = parse_desktop_file(valid_desktop_file)
        # Should have "Name" not "name" or "NAME"
        assert "Name" in result["Desktop Entry"]


class TestBuildUmuCommand:
    def test_basic_command(self):
        result = build_umu_command("GE-Proton", "/home/user/.wine", {}, "umu-run /path/to/game.exe")
        assert result == (
            'PROTONPATH="GE-Proton" WINEPREFIX="/home/user/.wine" '
            'STEAM_COMPAT_CONFIG="xalia" umu-run /path/to/game.exe'
        )

    def test_command_with_extra_config(self):
        config = {"GAMEID": "UMU-Test", "MANGOHUD": "1"}
        result = build_umu_command("GE-Proton", "/home/user/pfx", config, "umu-run /path/to/game.exe")
        assert 'PROTONPATH="GE-Proton"' in result
        assert 'WINEPREFIX="/home/user/pfx"' in result
        assert 'GAMEID="UMU-Test"' in result
        assert 'MANGOHUD="1"' in result
        assert "umu-run /path/to/game.exe" in result

    def test_excludes_protonpath_and_wineprefix_from_config(self):
        """PROTONPATH and WINEPREFIX from config dict should not be duplicated."""
        config = {
            "PROTONPATH": "Custom-Proton",
            "WINEPREFIX": "/custom/prefix",
            "GAMEID": "UMU-123",
        }
        result = build_umu_command("GE-Proton", "/home/user/pfx", config, "umu-run game.exe")
        # Should only have one PROTONPATH and one WINEPREFIX (from the explicit args)
        assert result.count('PROTONPATH="') == 1
        assert result.count('WINEPREFIX="') == 1
        assert 'GAMEID="UMU-123"' in result


class TestBuildUmuEnvPrefix:
    def test_basic_prefix(self):
        result = build_umu_env_prefix("GE-Proton", "/home/user/pfx", {})
        assert result == 'PROTONPATH="GE-Proton" WINEPREFIX="/home/user/pfx" STEAM_COMPAT_CONFIG="xalia" '

    def test_respects_user_steam_compat_config(self):
        config = {"STEAM_COMPAT_CONFIG": "noxalia"}
        result = build_umu_env_prefix("GE-Proton", "/home/user/pfx", config)
        assert result.count('STEAM_COMPAT_CONFIG="') == 1
        assert 'STEAM_COMPAT_CONFIG="noxalia" ' in result

    def test_prefix_with_extra_config(self):
        config = {"GAMEID": "UMU-456"}
        result = build_umu_env_prefix("GE-Proton", "/home/user/pfx", config)
        assert 'PROTONPATH="GE-Proton" WINEPREFIX="/home/user/pfx" ' in result
        assert 'GAMEID="UMU-456" ' in result

    def test_excludes_protonpath_wineprefix_from_config(self):
        config = {"PROTONPATH": "Bad", "WINEPREFIX": "/bad", "EXTRA": "val"}
        result = build_umu_env_prefix("GE-Proton", "/home/user/pfx", config)
        assert result.count('PROTONPATH="') == 1
        assert result.count('WINEPREFIX="') == 1
        assert 'EXTRA="val" ' in result


class TestBuildFlatpakExecCommand:
    def test_basic_command(self):
        result = build_flatpak_exec_command("/home/user/script.sh")
        assert result == 'flatpak run --command=sh org.gameyfin.Gameyfin-Desktop -c \'"/home/user/script.sh"\''

    def test_escapes_backslash(self):
        result = build_flatpak_exec_command('echo "test\\path"')
        assert '\\\\' in result

    def test_escapes_double_quotes(self):
        result = build_flatpak_exec_command('echo "hello"')
        assert '\\"' in result

    def test_escapes_dollar_sign(self):
        result = build_flatpak_exec_command('$HOME/game.sh')
        assert '\\$HOME' in result

    def test_escapes_backtick(self):
        result = build_flatpak_exec_command('echo `cmd`')
        assert '\\`cmd\\`' in result


class TestGetXdgUserDir:
    @pytest.fixture()
    def user_dirs_file(self, tmp_path):
        """Create a user-dirs.dirs file."""
        config_home = tmp_path / ".config"
        config_home.mkdir()
        dirs_file = config_home / "user-dirs.dirs"
        dirs_file.write_text('XDG_DESKTOP_DIR="$HOME/Desktop"\nXDG_DOWNLOAD_DIR="$HOME/Downloads"\n')
        return str(config_home)

    def test_desktop_dir(self, user_dirs_file):
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = user_dirs_file
        try:
            result = get_xdg_user_dir("DESKTOP")
            assert str(result).endswith("Desktop")
        finally:
            if old_xdg is not None:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            else:
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_download_dir(self, user_dirs_file):
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = user_dirs_file
        try:
            result = get_xdg_user_dir("DOWNLOAD")
            assert str(result).endswith("Downloads")
        finally:
            if old_xdg is not None:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            else:
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_unknown_dir_returns_fallback(self, user_dirs_file):
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = user_dirs_file
        try:
            result = get_xdg_user_dir("DOCUMENTS")
            # Should return fallback since key not found
            assert str(result).endswith("Documents")
        finally:
            if old_xdg is not None:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            else:
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_missing_config_file_returns_fallback(self, tmp_path):
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(tmp_path)
        try:
            result = get_xdg_user_dir("DESKTOP")
            assert str(result).endswith("Desktop")
        finally:
            if old_xdg is not None:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            else:
                os.environ.pop("XDG_CONFIG_HOME", None)


class TestReleaseYear:
    """release_year pulls the four-digit year out of whatever the server sends."""

    def test_iso_date(self):
        assert release_year("2019-05-14") == "2019"

    def test_bare_year(self):
        assert release_year("1998") == "1998"

    def test_no_year_present(self):
        assert release_year("coming soon") == ""

    def test_missing_release(self):
        assert release_year(None) == ""


class TestAccentColor:
    def test_prefers_the_qt_material_accent(self, monkeypatch, qtbot):
        from PyQt6.QtWidgets import QWidget

        from gameyfin_frontend.utils import accent_color

        monkeypatch.setenv("QTMATERIAL_PRIMARYCOLOR", "#ff9800")

        assert accent_color(QWidget()).name() == "#ff9800"

    def test_falls_back_to_the_palette_highlight(self, monkeypatch, qtbot):
        from PyQt6.QtGui import QColor, QPalette
        from PyQt6.QtWidgets import QWidget

        from gameyfin_frontend.utils import accent_color

        monkeypatch.delenv("QTMATERIAL_PRIMARYCOLOR", raising=False)
        widget = QWidget()
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#8bc34a"))
        widget.setPalette(palette)

        assert accent_color(widget).name() == "#8bc34a"

    def test_ignores_a_broken_material_accent(self, monkeypatch, qtbot):
        from PyQt6.QtGui import QColor, QPalette
        from PyQt6.QtWidgets import QWidget

        from gameyfin_frontend.utils import accent_color

        monkeypatch.setenv("QTMATERIAL_PRIMARYCOLOR", "not-a-colour")
        widget = QWidget()
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#8bc34a"))
        widget.setPalette(palette)

        assert accent_color(widget).name() == "#8bc34a"


class TestContrastingTextColor:
    def test_dark_text_on_a_light_background(self):
        from PyQt6.QtGui import QColor

        from gameyfin_frontend.utils import contrasting_text_color

        assert contrasting_text_color(QColor("#ffd740")).name() == "#000000"

    def test_light_text_on_a_dark_background(self):
        from PyQt6.QtGui import QColor

        from gameyfin_frontend.utils import contrasting_text_color

        assert contrasting_text_color(QColor("#3f51b5")).name() == "#ffffff"


class TestResolveScriptConfig:
    PER_SCRIPT = {
        "A.sh": {"GAMEID": "umu-1", "STORE": "none", "PROTONPATH": "P1",
                 "EXTRA_VARS": "FOO=a", "GAME_ARGS": "-a"},
        "B.sh": {"GAMEID": "umu-1", "STORE": "steam", "PROTONPATH": "P1",
                 "EXTRA_VARS": "FOO=b", "GAME_ARGS": ""},
    }

    def test_flat_config_applies_to_every_script(self):
        config = {"GAMEID": "umu-1", "STORE": "steam", "FOO": "bar", "GAME_ARGS": "-x"}
        assert resolve_script_config(config, "any.sh") == config

    def test_flat_config_default_has_no_game_args(self):
        config = {"GAMEID": "umu-1", "GAME_ARGS": "-x"}
        assert resolve_script_config(config) == {"GAMEID": "umu-1"}

    def test_flat_config_with_game_args_dict(self):
        config = {"GAMEID": "umu-1", "GAME_ARGS": {"ALL_SCRIPTS": "-all", "A.sh": "-a"}}
        assert resolve_script_config(config, "A.sh")["GAME_ARGS"] == "-a"
        assert resolve_script_config(config, "B.sh")["GAME_ARGS"] == "-all"

    def test_per_script_entries_stay_isolated(self):
        a = resolve_script_config(self.PER_SCRIPT, "A.sh")
        b = resolve_script_config(self.PER_SCRIPT, "B.sh")
        assert a == {"GAMEID": "umu-1", "PROTONPATH": "P1", "FOO": "a", "GAME_ARGS": "-a"}
        assert b == {"GAMEID": "umu-1", "STORE": "steam", "PROTONPATH": "P1", "FOO": "b"}

    def test_unknown_script_gets_shared_settings_without_args(self):
        new = resolve_script_config(self.PER_SCRIPT, "New.sh")
        assert new["GAMEID"] == "umu-1"
        assert new["PROTONPATH"] == "P1"
        assert "GAME_ARGS" not in new

    def test_all_scripts_baseline_with_overrides(self):
        config = {
            "ALL_SCRIPTS": {"GAMEID": "umu-1", "EXTRA_VARS": "FOO=base", "GAME_ARGS": "-all"},
            "A.sh": {"GAME_ARGS": "-a"},
        }
        assert resolve_script_config(config, "A.sh") == {"GAMEID": "umu-1", "FOO": "base", "GAME_ARGS": "-a"}
        assert resolve_script_config(config, "B.sh")["GAME_ARGS"] == "-all"

    def test_env_prefix_never_contains_script_names(self):
        result = build_umu_env_prefix("P", "/pfx", resolve_script_config(self.PER_SCRIPT, "A.sh"))
        assert "A.sh" not in result and "B.sh" not in result and "EXTRA_VARS" not in result

    def test_script_settings_keeps_dialog_shape(self):
        fields = script_settings({"GAMEID": "umu-1", "FOO": "bar", "BAZ": "1"}, "A.sh")
        assert fields["EXTRA_VARS"] == "FOO=bar\nBAZ=1"
        assert fields["GAMEID"] == "umu-1"


class TestXaliaToggle:
    def test_on_by_default(self):
        assert 'STEAM_COMPAT_CONFIG="xalia" ' in build_umu_env_prefix("P", "/pfx", {})

    def test_disabled(self):
        result = build_umu_env_prefix("P", "/pfx", {"ENABLE_XALIA": "0"})
        assert "STEAM_COMPAT_CONFIG" not in result
        assert "ENABLE_XALIA" not in result

    def test_enabled_is_not_an_env_var(self):
        result = build_umu_env_prefix("P", "/pfx", {"ENABLE_XALIA": "1"})
        assert 'STEAM_COMPAT_CONFIG="xalia" ' in result
        assert "ENABLE_XALIA" not in result
