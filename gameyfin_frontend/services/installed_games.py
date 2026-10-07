"""Link installed games (Wine prefixes) to the Gameyfin game they came from.

Prefixes are discovered from folder names only, so nothing on disk says which
server game a prefix belongs to. This service keeps that link in a small
``gameyfin.json`` next to the game's ``config.json`` in its shortcut scripts
directory — deleting the prefix (which removes that directory) drops the link
with it. Prefixes installed before the link existed are matched once through
the download history or the game title, and the result is written back.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from gameyfin_frontend.services.download_history_service import DownloadHistoryService
from gameyfin_frontend.services.prefix_service import PrefixService
from gameyfin_frontend.utils import sanitize_name

logger = logging.getLogger(__name__)

LINK_FILENAME = "gameyfin.json"

_DOWNLOAD_ID_RE = re.compile(r"/download/(\d+)(?:[/?#]|$)")


def game_id_from_url(url: str | None) -> int | None:
    """Return the game id in a Gameyfin download URL (``/download/<id>?...``)."""
    if not url:
        return None
    match = _DOWNLOAD_ID_RE.search(url)
    return int(match.group(1)) if match else None


@dataclass
class InstalledGame:
    """An installed game: its prefix, launch scripts and remembered script."""

    game_id: int
    game_name: str
    prefix_path: str
    scripts: list[str] = field(default_factory=list)
    last_script: str | None = None
    # Title remembered in the link (falls back to the folder name)
    title: str = ""
    # The server's game data (``Game.to_dict()``), kept for offline use
    details: dict[str, Any] | None = None


class InstalledGamesService:
    """Reads and writes the prefix ↔ Gameyfin game links."""

    def __init__(self, settings: Any) -> None:
        """Initialize the service.

        Args:
            settings: SettingsManager instance providing app configuration.
        """
        self.settings = settings

    # ------------------------------------------------------------------
    # Link file
    # ------------------------------------------------------------------

    def _link_path(self, game_name: str) -> str:
        return os.path.join(self.settings.get_shortcuts_dir(game_name), LINK_FILENAME)

    def read_link(self, game_name: str) -> dict[str, Any]:
        """Return the stored link for *game_name*, or an empty dict."""
        for scripts_dir in self.settings.get_shortcuts_dirs(game_name):
            path = os.path.join(scripts_dir, LINK_FILENAME)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Could not read %s: %s", path, e)
        return {}

    def _write_link(self, game_name: str, updates: dict[str, Any]) -> None:
        data = self.read_link(game_name)
        data.update(updates)
        path = self._link_path(game_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=4)

    def link(self, game_name: str, game_id: int, title: str = "") -> None:
        """Record that the prefix of *game_name* holds Gameyfin game *game_id*."""
        updates: dict[str, Any] = {"game_id": int(game_id)}
        if title:
            updates["title"] = title
        self._write_link(game_name, updates)
        logger.info("Linked '%s' to Gameyfin game %s", game_name, game_id)

    def save_details(self, game_name: str, details: dict[str, Any]) -> None:
        """Store the server's data for *game_name*, so it can be shown offline."""
        updates: dict[str, Any] = {"game": details}
        if details.get("title"):
            updates["title"] = details["title"]
        self._write_link(game_name, updates)

    def set_last_script(self, game_name: str, script: str) -> None:
        """Remember *script* (a basename) as the one Play should run."""
        try:
            self._write_link(game_name, {"last_script": os.path.basename(script)})
        except OSError as e:
            logger.error("Could not save the selected script for '%s': %s", game_name, e)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scripts_for(self, game_name: str) -> list[str]:
        """Return the game's launch scripts (full paths), sorted."""
        scripts: list[str] = []
        for scripts_dir in self.settings.get_shortcuts_dirs(game_name):
            if os.path.isdir(scripts_dir):
                scripts.extend(glob.glob(os.path.join(scripts_dir, "*.sh")))
        return sorted(scripts)

    def scan(self, games: list[Any] | None = None,
             history: list[dict[str, Any]] | None = None) -> dict[int, InstalledGame]:
        """Return the installed games keyed by Gameyfin game id.

        Prefixes without a link are matched through *history* (download
        records, loaded from ``downloads.json`` when not given) and then by
        title against *games*; a match is written back as a link.
        """
        try:
            prefixes = PrefixService(self.settings).get_all_prefixes()
        except OSError as e:
            logger.error("Could not list prefixes: %s", e)
            return {}

        history_ids: dict[str, int] | None = None
        titles = {sanitize_name(g.title).lower(): g for g in games or []}

        installed: dict[int, InstalledGame] = {}
        for prefix_name, prefix_path in sorted(prefixes.items()):
            game_name = prefix_name.removesuffix("_pfx")
            link = self.read_link(game_name)
            game_id = link.get("game_id")

            if not isinstance(game_id, int):
                if history_ids is None:
                    history_ids = self._history_ids(history)
                game_id = history_ids.get(prefix_name)
                title = next((g.title for g in games or [] if g.id == game_id), "")
                if game_id is None and game_name in titles:
                    game = titles[game_name]
                    game_id, title = game.id, game.title
                if game_id is None:
                    continue
                try:
                    self.link(game_name, game_id, title)
                except OSError as e:
                    logger.error("Could not link '%s': %s", game_name, e)
                link = {**link, "title": title}

            installed[game_id] = InstalledGame(
                game_id=game_id,
                game_name=game_name,
                prefix_path=prefix_path,
                scripts=self.scripts_for(game_name),
                last_script=link.get("last_script"),
                title=link.get("title") or game_name,
                details=link.get("game") if isinstance(link.get("game"), dict) else None,
            )
        return installed

    def _history_ids(self, history: list[dict[str, Any]] | None) -> dict[str, int]:
        """Map prefix folder names to game ids using the download history."""
        if history is None:
            history = DownloadHistoryService(self.settings.get_downloads_json_path()).load()
        ids: dict[str, int] = {}
        for record in history:
            game_id = record.get("game_id")
            if not isinstance(game_id, int):
                game_id = game_id_from_url(record.get("url"))
            path = record.get("path")
            if game_id is None or not path:
                continue
            # Same derivation as GameInstaller.build_wine_prefix
            ids.setdefault(f"{os.path.basename(path).lower()}_pfx", game_id)
        return ids
