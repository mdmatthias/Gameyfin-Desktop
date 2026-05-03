import json
import logging
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List

import requests

from .settings import settings_manager

logger = logging.getLogger(__name__)

class UmuDatabase:
    def __init__(self):
        if sys.platform == "win32":
            logger.info("Running on Windows. UmuDatabase disabled.")
            self.umu_api_url = ""
            self._games_by_title = {}
            return

        # Stores data as: {"Game Title": [entry1, entry2, ...]}
        self._games_by_title: Dict[str, List[dict]] = defaultdict(list)
        self.cache_file_path = settings_manager.get_umu_cache_path()

        logger.info("Initializing Umu database...")
        self._load_cache_from_disk()
        self.refresh_cache()
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
        into the _games_by_title dict.
        """
        self._games_by_title.clear()

        if not isinstance(all_entries_raw, list):
            logger.error(
                "Initial data fetch did not return a list. Cache will be empty. (Received: %s)", type(all_entries_raw))
            return

        for entry in all_entries_raw:
            title = entry.get("title")
            if title:
                self._games_by_title[title].append(entry)
        self._save_cache_to_disk()

    def _load_cache_from_disk(self):
        """Loads the cached Umu database from a local JSON file."""
        if os.path.exists(self.cache_file_path):
            try:
                with open(self.cache_file_path, 'r') as f:
                    data = json.load(f)
                    self._games_by_title = defaultdict(list, data)
                logger.info("UmuDatabase: Loaded cache from %s", self.cache_file_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("UmuDatabase: Failed to load cache from disk: %s", e)

    def _save_cache_to_disk(self):
        """Saves the current title cache to a local JSON file."""
        try:
            with open(self.cache_file_path, 'w') as f:
                json.dump(dict(self._games_by_title), f)
            logger.info("UmuDatabase: Cache saved to %s", self.cache_file_path)
        except OSError as e:
            logger.error("UmuDatabase: Failed to save cache to disk: %s", e)

    def refresh_cache(self):
        """
        Fetches the full list from the API and rebuilds the local title cache.
        """
        if sys.platform == "win32":
            return

        logger.info("Refreshing UmuDatabase cache...")
        try:
            all_entries_raw = self.list_all()
            if isinstance(all_entries_raw, list):
                self._build_title_cache(all_entries_raw)
                logger.info("Cache refresh complete.")
        except Exception as e:
            logger.error("UmuDatabase: Failed to refresh cache: %s. Proceeding with empty cache.", e)

    def _request_umu_api(self, params=None):
        """
        Helper function to make a GET request and parse the JSON response.
        """
        response = None
        try:
            umu_api_url = settings_manager.get("GF_UMU_API_URL")
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

        Returns a list of all matching entries.
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

    def list_all_by_store(self, store: str):
        """
        List ALL entries based on STORE
        API: /umu_api.php?store=SOME-STORE
        """
        return self._request_umu_api(params={"store": store.lower()})

    def get_title_and_umu_id_by_store_and_codename(self, store: str, codename: str):
        """
        Get TITLE and UMU_ID based on STORE and CODENAME
        API: /umu_api.php?store=SOME-STORE&codename=SOME-CODENAME-OR-APP-ID
        """
        return self._request_umu_api(params={"store": store.lower(), "codename": codename.lower()})

    def get_game_by_codename(self, codename: str) -> List:
        """
        Get ALL GAME VALUES based on CODENAME
        API: /umu_api.php?codename=SOME-CODENAME-OR-APP-ID
        """
        return self._request_umu_api(params={"codename": codename.lower()})

    def get_title_by_store_and_umu_id(self, store: str, umu_id: str):
        """
        Get TITLE based on UMU_ID and STORE
        API: /umu_api.php?umu_id=SOME-UMU-ID&store=SOME-STORE-OR-NONE
        """
        return self._request_umu_api(params={"store": store.lower(), "umu_id": umu_id.lower()})

    def get_game_by_umu_id(self, umu_id: str):
        """
        Get ALL GAME VALUES AND ENTRIES based on UMU_ID
        API: /umu_api.php?umu_id=SOME-UMU-ID
        """
        return self._request_umu_api(params={"umu_id": umu_id.lower()})

    def get_umu_id_by_title_and_store(self, title: str, store: str):
        """
        Get UMU_ID based on TITLE and STORE
        API: /umu_api.php?title=SOME-GAME-TITLE&STORE=SOME-STORE
        (Note: Title is not lowercased as it may be case-sensitive)
        """
        return self._request_umu_api(params={"title": title, "store": store.lower()})

    def get_umu_id_by_title(self, title: str):
        """
        Get UMU_ID based on TITLE and no store
        API: /umu_api.php?title=SOME-GAME-TITLE
        (Note: Title is not lowercased as it may be case-sensitive)
        """
        return self._request_umu_api(params={"title": title})
