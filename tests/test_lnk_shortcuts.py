"""Tests for launch scripts built from Proton shortcuts that point at a Windows .lnk."""

from __future__ import annotations

import os
import struct
import subprocess
from unittest.mock import MagicMock

import pytest

from gameyfin_frontend.utils import (
    create_shortcuts,
    lnk_args_for_script,
    parse_lnk,
    split_windows_args,
    windows_args_to_shell,
)

LNK_CLSID = b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"
DOSBOX_ARGS = '-conf "..\\dosbox_game.conf" -conf "..\\dosbox_game_single.conf" -noconsole -c "exit"'


def make_lnk(path, target: str, arguments: str = "", nul_terminated: bool = False) -> None:
    """Write a minimal unicode shell link with LinkInfo and optional Arguments."""
    flags = 0x2 | 0x80 | (0x20 if arguments else 0)
    header = struct.pack("<I", 0x4C) + LNK_CLSID + struct.pack("<I", flags) + b"\0" * (0x4C - 24)

    base = target.encode("latin-1") + b"\0"
    info_header_size = 0x1C
    link_info = struct.pack(
        "<7I", info_header_size + len(base) + 1, info_header_size, 0x1, 0,
        info_header_size, 0, info_header_size + len(base),
    ) + base + b"\0"

    string_data = b""
    if arguments:
        value = arguments + ("\0" if nul_terminated else "")
        string_data = struct.pack("<H", len(value)) + value.encode("utf-16-le")

    path.write_bytes(header + link_info + string_data)


@pytest.fixture()
def prefix(tmp_path):
    """A wine prefix whose C: drive maps to drive_c, with a GOG-style DOSBox install."""
    pfx = tmp_path / "prefixes" / "my game_pfx"
    drive_c = pfx / "drive_c"
    dosbox = drive_c / "GOG Games" / "My Game" / "DOSBOX"
    dosbox.mkdir(parents=True)
    (dosbox / "DOSBox.exe").touch()
    (pfx / "dosdevices").mkdir()
    os.symlink(drive_c, pfx / "dosdevices" / "c:")

    public_desktop = drive_c / "users" / "Public" / "Desktop"
    public_desktop.mkdir(parents=True)
    make_lnk(public_desktop / "My Game.lnk", "C:\\GOG Games\\My Game\\DOSBOX\\DOSBox.exe",
             DOSBOX_ARGS, nul_terminated=True)

    shortcuts = drive_c / "proton_shortcuts"
    shortcuts.mkdir()
    # What Proton writes: Exec only references the .lnk, StartupWMClass is lowercased.
    (shortcuts / "My Game.desktop").write_text(
        "[Desktop Entry]\nName=My Game\n"
        'Exec="C:\\\\\\\\users\\\\\\\\Public\\\\\\\\Desktop\\\\\\\\My Game.lnk"\n'
        f"Path={dosbox}\nStartupWMClass=dosbox.exe\n"
    )
    return pfx


def shell_words(shell_args: str) -> list[str]:
    """Return the argv a generated script line would pass, as /bin/sh parses it."""
    out = subprocess.run(["sh", "-c", f'printf "%s\\n" {shell_args}'],
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()


class TestParseLnk:
    def test_reads_target_and_arguments(self, tmp_path):
        lnk = tmp_path / "game.lnk"
        make_lnk(lnk, "C:\\Games\\game.exe", "-windowed")
        assert parse_lnk(str(lnk)) == ("C:\\Games\\game.exe", "-windowed")

    def test_strips_nul_terminator_counted_in_length(self, tmp_path):
        lnk = tmp_path / "game.lnk"
        make_lnk(lnk, "C:\\Games\\game.exe", "-windowed", nul_terminated=True)
        assert parse_lnk(str(lnk)) == ("C:\\Games\\game.exe", "-windowed")

    def test_no_arguments(self, tmp_path):
        lnk = tmp_path / "game.lnk"
        make_lnk(lnk, "C:\\Games\\game.exe")
        assert parse_lnk(str(lnk)) == ("C:\\Games\\game.exe", "")

    def test_not_a_shell_link(self, tmp_path):
        bogus = tmp_path / "bogus.lnk"
        bogus.write_bytes(b"not a link")
        assert parse_lnk(str(bogus)) is None


class TestWindowsArgs:
    @pytest.mark.parametrize("command_line, expected", [
        (DOSBOX_ARGS, ["-conf", "..\\dosbox_game.conf", "-conf", "..\\dosbox_game_single.conf",
                       "-noconsole", "-c", "exit"]),
        ('"a b"  c', ["a b", "c"]),
        ('C:\\dir\\ x', ["C:\\dir\\", "x"]),
        ('"C:\\dir\\\\" x', ["C:\\dir\\", "x"]),
        ('say \\"hi\\"', ["say", '"hi"']),
        ('"a""b"', ['a"b']),
        ('""', [""]),
        ("", []),
    ])
    def test_split_like_commandlinetoargvw(self, command_line, expected):
        assert split_windows_args(command_line) == expected

    def test_shell_text_round_trips_through_sh(self):
        shell = windows_args_to_shell(DOSBOX_ARGS + ' "$HOME `x`"')
        assert shell_words(shell) == split_windows_args(DOSBOX_ARGS) + ["$HOME `x`"]


@pytest.mark.usefixtures("tmp_app_data")
class TestCreateShortcutsUsesLnk:
    def test_script_gets_lnk_arguments_and_real_exe_case(self, prefix, tmp_path):
        scripts_dir = tmp_path / "scripts"
        create_shortcuts(
            all_desktop_files=[str(prefix / "drive_c" / "proton_shortcuts" / "My Game.desktop")],
            scripts_dir=str(scripts_dir),
            wine_prefix=str(prefix),
            install_config={"PROTONPATH": "GE-Proton", "GAME_ARGS": ""},
        )

        line = (scripts_dir / "My Game.sh").read_text().rstrip().splitlines()[-1]
        exe = str(prefix / "drive_c" / "GOG Games" / "My Game" / "DOSBOX" / "DOSBox.exe")
        _, args = line.split(f'umu-run "{exe}" ', 1)
        assert shell_words(args) == split_windows_args(DOSBOX_ARGS)

    def test_user_game_args_override_lnk_arguments(self, prefix, tmp_path):
        scripts_dir = tmp_path / "scripts"
        create_shortcuts(
            all_desktop_files=[str(prefix / "drive_c" / "proton_shortcuts" / "My Game.desktop")],
            scripts_dir=str(scripts_dir),
            wine_prefix=str(prefix),
            install_config={"PROTONPATH": "GE-Proton", "GAME_ARGS": "-custom"},
        )

        script = (scripts_dir / "My Game.sh").read_text()
        assert script.rstrip().endswith('DOSBox.exe" -custom')
        assert "-conf" not in script

    def test_desktop_without_lnk_is_unchanged(self, tmp_path):
        game_dir = tmp_path / "game"
        game_dir.mkdir()
        desktop = tmp_path / "Game.desktop"
        desktop.write_text(f"[Desktop Entry]\nName=Game\nExec=game.exe\nPath={game_dir}\nStartupWMClass=game.exe\n")

        scripts_dir = tmp_path / "scripts"
        create_shortcuts([str(desktop)], str(scripts_dir), str(tmp_path / "pfx"), {})
        assert (scripts_dir / "Game.sh").read_text().rstrip().endswith('game.exe"')


@pytest.mark.usefixtures("tmp_app_data")
class TestUpdateScriptsKeepsLnkArgs:
    def test_lnk_args_for_script_matches_sanitized_name(self, prefix):
        assert shell_words(lnk_args_for_script("My Game.sh", str(prefix))) == split_windows_args(DOSBOX_ARGS)
        assert lnk_args_for_script("Other.sh", str(prefix)) == ""

    def test_update_scripts_keeps_lnk_args_without_user_args(self, prefix, tmp_path):
        from gameyfin_frontend.services.prefix_service import PrefixService

        scripts_dir = tmp_path / "scripts"
        create_shortcuts(
            all_desktop_files=[str(prefix / "drive_c" / "proton_shortcuts" / "My Game.desktop")],
            scripts_dir=str(scripts_dir),
            wine_prefix=str(prefix),
            install_config={},
        )
        settings = MagicMock()
        settings.get_shortcuts_dirs.return_value = [str(scripts_dir)]
        settings.get.return_value = None

        assert PrefixService(settings).update_scripts(str(prefix), {"GAME_ARGS": ""}, "my game") == 1
        script = (scripts_dir / "My Game.sh").read_text()
        assert script.count("-conf") == 2
        assert "dosbox_game_single.conf" in script
