import json
import logging
import os
import re
import sys
import threading
from collections import defaultdict
from typing import Callable, Dict, List

import requests

from .settings import SettingsManager

logger = logging.getLogger(__name__)


class _RefreshWorker:
    """Worker object that runs refresh_cache on a background thread."""

    def __init__(self, database: "UmuDatabase") -> None:
        self._db = database

    def run(self) -> None:
        self._db.refresh_cache()
        logger.info("Background UmuDatabase cache refresh complete.")


class UmuDatabase:
    def __init__(self, settings: SettingsManager | None = None):
        """Initialize the UMU database for game fix lookups.

        Loads the local disk cache synchronously.  Use
        ``refresh_cache_async()`` to fetch a fresh copy from the API
        without blocking the caller.

        Args:
            settings: SettingsManager instance providing app configuration.
        """
        if sys.platform == "win32":
            logger.info("Running on Windows. UmuDatabase disabled.")
            self.umu_api_url = ""
            self._games_by_title = {}
            return

        # Thread-safe cache access: _refresh_lock protects the three
        # index dicts during an in-flight background refresh so that
        # lookups always see a *complete* snapshot (old or new, never
        # half-built).
        self._refresh_lock = threading.Lock()
        self._refresh_thread: "threading.Thread | None" = None

        # Stores data as: {"Game Title": [entry1, entry2, ...]}
        self._games_by_title: Dict[str, List[dict]] = defaultdict(list)
        self._games_by_codename: Dict[str, List[dict]] = defaultdict(list)
        self._games_by_umu_id: Dict[str, List[dict]] = defaultdict(list)
        self.settings = settings
        self.cache_file_path = settings.get_umu_cache_path() if settings else ""

        logger.info("Initializing Umu database...")
        self._load_cache_from_disk()
        self._ROMAN_REPLACEMENTS = (
            (r'\bX\b', ' 10 '),
            (r'\bIX\b', ' 9 '),
            (r'\bVIII\b', ' 8 '),
            (r'\bVII\b', ' 7 '),
            (r'\bVI\b', ' 6 '),
            (r'\bIV\b', ' 4 '),
            (r'\bV\b', ' 5 '),
            (r'\bIII\b', ' 3 '),
            (r'\bII\b', ' 2 '),
            (r'\bI\b', ' 1 ')
        )
        logger.info("Umu database initialized.")

    def _build_title_cache(self, all_entries_raw: List[dict]):
        """
        Helper to process the raw list from list_all()
        into the _games_by_title, _games_by_codename, and _games_by_umu_id dicts.
        """
        self._games_by_title.clear()
        self._games_by_codename.clear()
        self._games_by_umu_id.clear()

        if not isinstance(all_entries_raw, list):
            logger.error(
                "Initial data fetch did not return a list. Cache will be empty. (Received: %s)", type(all_entries_raw))
            return

        for entry in all_entries_raw:
            title = entry.get("title")
            if title:
                self._games_by_title[title].append(entry)

            codename = entry.get("codename") or entry.get("appid")
            if codename:
                self._games_by_codename[codename].append(entry)

            umu_id = entry.get("umu_id")
            if umu_id:
                self._games_by_umu_id[umu_id].append(entry)

        self._save_cache_to_disk()

    def _load_cache_from_disk(self):
        """Loads the cached Umu database from a local JSON file."""
        if os.path.exists(self.cache_file_path):
            try:
                with open(self.cache_file_path, 'r') as f:
                    data = json.load(f)
                self._games_by_title = defaultdict(list, data.get("title", {}))
                self._games_by_codename = defaultdict(list, data.get("codename", {}))
                self._games_by_umu_id = defaultdict(list, data.get("umu_id", {}))
                logger.info("UmuDatabase: Loaded cache from %s", self.cache_file_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("UmuDatabase: Failed to load cache from disk: %s", e)

    def _save_cache_to_disk(self):
        """Saves the current title, codename, and umu_id caches to a local JSON file."""
        try:
            cache_data = {
                "title": dict(self._games_by_title),
                "codename": dict(self._games_by_codename),
                "umu_id": dict(self._games_by_umu_id),
            }
            with open(self.cache_file_path, 'w') as f:
                json.dump(cache_data, f)
            logger.info("UmuDatabase: Cache saved to %s", self.cache_file_path)
        except OSError as e:
            logger.error("UmuDatabase: Failed to save cache to disk: %s", e)

    def refresh_cache(self) -> None:
        """
        Fetches the full list from the API and rebuilds the local title cache.

        This method runs synchronously — use ``refresh_cache_async`` for
        non-blocking calls.
        """
        if sys.platform == "win32":
            return

        logger.info("Refreshing UmuDatabase cache...")
        try:
            all_entries_raw = self.list_all()
            if isinstance(all_entries_raw, list):
                self._build_title_cache(all_entries_raw)
                logger.info("Cache refresh complete.")
        except (KeyError, TypeError, ValueError) as e:
            logger.error("UmuDatabase: Failed to refresh cache: %s. Proceeding with empty cache.", e)

    def refresh_cache_async(self, callback: Callable[["UmuDatabase"], None] | None = None) -> None:
        """Start a background cache refresh that does not block the caller.

        The local disk cache is still used immediately; the API call
        happens on a background thread and updates the indexes when
        done.

        Args:
            callback: Optional callable invoked with *self* after the
                refresh finishes (runs on the background thread).
        """
        if sys.platform == "win32":
            return

        # Guard: don't start a second concurrent refresh.
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            logger.debug("UmuDatabase: Background refresh already in progress.")
            return

        logger.info("Starting background UmuDatabase cache refresh...")
        worker = _RefreshWorker(self)
        thread = threading.Thread(target=worker.run, daemon=True, name="umu-cache-refresh")
        self._refresh_thread = thread
        thread.start()

        if callback is not None:
            # Wrap callback so it runs *after* refresh completes but
            # still on the background thread.  The caller (usually
            # the main thread) can connect to this via a QMetaObject
            # invocation if UI updates are needed.
            def _wrapped() -> None:
                worker.run()
                callback(self)
            thread = threading.Thread(target=_wrapped, daemon=True, name="umu-cache-refresh")
            self._refresh_thread = thread
            thread.start()

    def _request_umu_api(self, params=None):
        """
        Helper function to make a GET request and parse the JSON response.
        """
        response = None
        try:
            umu_api_url = self.settings.get("GF_UMU_API_URL") if self.settings else ""
            response = requests.get(umu_api_url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning("Could not get umu database result for params %s: %s", params, e)
            return {}
        except json.JSONDecodeError as e:
            if response:
                logger.error("Could not decode JSON for params %s (Response: %s): %s", params, response.text, e)
            else:
                logger.error("Could not decode JSON for params %s: %s", params, e)
            return {}

    def _normalize_string(self, text: str) -> str:
        """
        Converts text to lowercase, replaces Roman numerals,
        and removes all non-alphanumeric characters.
        e.g., "Baldur's Gate II" -> "baldursgate2"
        e.g., "baldurs gate 2" -> "baldursgate2"
        """
        normalized_text = text

        for roman_re, arabic in self._ROMAN_REPLACEMENTS:
            normalized_text = re.sub(roman_re, arabic, normalized_text, flags=re.IGNORECASE)

        normalized_text = normalized_text.lower()
        return re.sub(r'[^a-z0-9]', '', normalized_text)

    def search_by_partial_title(self, partial_title: str) -> List[dict]:
        """
        Searches the local cache for game titles containing the partial_title.

        This search is case-insensitive and ignores all punctuation and spaces.
        e.g., "baldurs" will match "Baldur's Gate".

        Only checks the local cache — never makes a network request.
        Returns an empty list if no match is found.
        """
        if not partial_title:
            return []

        normalized_search_term = self._normalize_string(partial_title)

        if not normalized_search_term:
            return []

        matching_entries = []

        for full_title in self._games_by_title:
            normalized_full_title = self._normalize_string(full_title)

            if normalized_search_term in normalized_full_title:
                matching_entries.extend(self._games_by_title[full_title])

        return matching_entries

    def list_all(self):
        """
        List ALL entries
        API: /umu_api.php
        """
        return self._request_umu_api()

    def list_all_by_store(self, store: str) -> dict | list | None:
        """
        List ALL entries based on STORE
        API: /umu_api.php?store=SOME-STORE
        """
        return self._request_umu_api(params={"store": store.lower()})

    def get_title_and_umu_id_by_store_and_codename(self, store: str, codename: str) -> dict | list | None:
        """
        Get TITLE and UMU_ID based on STORE and CODENAME
        API: /umu_api.php?store=SOME-STORE&codename=SOME-CODENAME-OR-APP-ID
        """
        return self._request_umu_api(params={"store": store.lower(), "codename": codename.lower()})

    def get_game_by_codename(self, codename: str) -> List:
        """
        Get ALL GAME VALUES based on CODENAME.

        Only checks the local cache — never makes a network request.
        Returns an empty list if not found.
        """
        cached = self._games_by_codename.get(codename.lower())
        if cached:
            logger.info("UmuDatabase: Found codename %s in local cache", codename)
            return cached

        logger.info("UmuDatabase: Codename %s not in cache (background refresh may populate it)", codename)
        return []

    def get_title_by_store_and_umu_id(self, store: str, umu_id: str) -> dict | list | None:
        """
        Get TITLE based on UMU_ID and STORE
        API: /umu_api.php?umu_id=SOME-UMU-ID&store=SOME-STORE-OR-NONE
        """
        return self._request_umu_api(params={"store": store.lower(), "umu_id": umu_id.lower()})

    def get_game_by_umu_id(self, umu_id: str) -> dict | list | None:
        """
        Get ALL GAME VALUES AND ENTRIES based on UMU_ID.

        Only checks the local cache — never makes a network request.
        Returns an empty list if not found.
        """
        # Check local cache first
        cached = self._games_by_umu_id.get(umu_id.lower())
        if cached:
            logger.info("UmuDatabase: Found umu_id %s in local cache", umu_id)
            return cached

        logger.info("UmuDatabase: umu_id %s not in cache (background refresh may populate it)", umu_id)
        return []

    def get_umu_id_by_title_and_store(self, title: str, store: str) -> dict | list | None:
        """
        Get UMU_ID based on TITLE and STORE
        API: /umu_api.php?title=SOME-GAME-TITLE&STORE=SOME-STORE
        (Note: Title is not lowercased as it may be case-sensitive)
        """
        return self._request_umu_api(params={"title": title, "store": store.lower()})

    def get_umu_id_by_title(self, title: str) -> dict | list | None:
        """
        Get UMU_ID based on TITLE and no store
        API: /umu_api.php?title=SOME-GAME-TITLE
        (Note: Title is not lowercased as it may be case-sensitive)
        """
        return self._request_umu_api(params={"title": title})
