"""Tests for linking installed prefixes to Gameyfin games."""

import json
import os

import pytest

from gameyfin_frontend.services.gameyfin_api import Game
from gameyfin_frontend.services.installed_games import (LINK_FILENAME,
                                                        InstalledGamesService,
                                                        game_id_from_url)


def _make_prefix(settings, game_name: str, scripts: tuple[str, ...] = ()) -> str:
    """Create a prefix folder (and optional launch scripts) for *game_name*."""
    prefix = os.path.join(settings.get_prefixes_dir(), f"{game_name}_pfx")
    os.makedirs(prefix)
    scripts_dir = settings.get_shortcuts_dir(game_name)
    os.makedirs(scripts_dir, exist_ok=True)
    for script in scripts:
        with open(os.path.join(scripts_dir, script), "w") as f:
            f.write("#!/bin/sh\n")
    return prefix


def _read_link(settings, game_name: str) -> dict:
    with open(os.path.join(settings.get_shortcuts_dir(game_name), LINK_FILENAME)) as f:
        return json.load(f)


class TestGameIdFromUrl:
    @pytest.mark.parametrize("url, expected", [
        ("http://srv/download/7?provider=fs", 7),
        ("https://srv:8080/download/123", 123),
        ("http://srv/download/42/", 42),
        ("http://srv/images/cover/7", None),
        ("http://srv/download/abc", None),
        ("", None),
        (None, None),
    ])
    def test_parses_the_download_path(self, url, expected):
        assert game_id_from_url(url) == expected


class TestInstalledGamesService:
    def test_linked_prefix_is_reported_with_its_scripts(self, fresh_settings):
        prefix = _make_prefix(fresh_settings, "dark earth", ("B.sh", "A.sh"))
        service = InstalledGamesService(fresh_settings)
        service.link("dark earth", 7, "Dark Earth")

        installed = service.scan(history=[])

        assert list(installed) == [7]
        info = installed[7]
        assert info.game_name == "dark earth"
        assert info.prefix_path == prefix
        assert [os.path.basename(s) for s in info.scripts] == ["A.sh", "B.sh"]
        assert _read_link(fresh_settings, "dark earth") == {"game_id": 7, "title": "Dark Earth"}

    def test_unlinked_prefix_without_a_match_is_skipped(self, fresh_settings):
        _make_prefix(fresh_settings, "mystery")

        assert InstalledGamesService(fresh_settings).scan(history=[]) == {}

    def test_backfills_from_the_download_history(self, fresh_settings, tmp_path):
        _make_prefix(fresh_settings, "dark earth")
        history = [{"path": str(tmp_path / "Dark Earth"), "url": "http://srv/download/9?provider=fs"}]

        installed = InstalledGamesService(fresh_settings).scan(history=history)

        assert list(installed) == [9]
        assert _read_link(fresh_settings, "dark earth")["game_id"] == 9

    def test_history_is_read_from_downloads_json(self, fresh_settings, tmp_path):
        _make_prefix(fresh_settings, "dark earth")
        with open(fresh_settings.get_downloads_json_path(), "w") as f:
            json.dump([{"path": str(tmp_path / "dark earth"), "game_id": 11,
                        "url": "file:///elsewhere"}], f)

        assert list(InstalledGamesService(fresh_settings).scan()) == [11]

    def test_backfills_by_title(self, fresh_settings):
        _make_prefix(fresh_settings, "some game deluxe")
        games = [Game(id=3, title="Some Game: Deluxe", library_id=1), Game(id=4, title="Other", library_id=1)]

        installed = InstalledGamesService(fresh_settings).scan(games, history=[])

        assert list(installed) == [3]
        assert _read_link(fresh_settings, "some game deluxe") == {
            "game_id": 3, "title": "Some Game: Deluxe"}

    def test_existing_link_wins_over_backfill(self, fresh_settings, tmp_path):
        _make_prefix(fresh_settings, "alpha")
        service = InstalledGamesService(fresh_settings)
        service.link("alpha", 1)
        history = [{"path": str(tmp_path / "alpha"), "url": "http://srv/download/2"}]

        assert list(service.scan([Game(id=5, title="Alpha", library_id=1)], history=history)) == [1]

    def test_last_script_is_merged_into_the_link(self, fresh_settings):
        _make_prefix(fresh_settings, "alpha", ("Run.sh", "Setup.sh"))
        service = InstalledGamesService(fresh_settings)
        service.link("alpha", 1, "Alpha")

        service.set_last_script("alpha", "/somewhere/Setup.sh")

        assert _read_link(fresh_settings, "alpha") == {
            "game_id": 1, "title": "Alpha", "last_script": "Setup.sh"}
        assert service.scan(history=[])[1].last_script == "Setup.sh"

    def test_deleted_prefix_drops_out(self, fresh_settings):
        from gameyfin_frontend.services.prefix_service import PrefixService

        prefix = _make_prefix(fresh_settings, "alpha")
        service = InstalledGamesService(fresh_settings)
        service.link("alpha", 1)

        PrefixService(fresh_settings).delete_prefix(prefix, "alpha")

        assert service.scan(history=[]) == {}
