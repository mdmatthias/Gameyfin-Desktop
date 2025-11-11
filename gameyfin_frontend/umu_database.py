import re
from collections import defaultdict
from os import getenv
from typing import Dict, List

import requests


class UmuDatabase:
    def __init__(self):
        self.umu_api_url = getenv("GF_UMU_API_URL", "https://umu.openwinecomponents.org/umu_api.php")

        # Stores data as: {"Game Title": [entry1, entry2, ...]}
        self._games_by_title: Dict[str, List[dict]] = defaultdict(list)

        print("Initializing Umu database and fetching all entries...")
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
        print(f"Umu database initialized.")

    def _build_title_cache(self, all_entries_raw: List[dict]):
        """
        Helper to process the raw list from list_all()
        into the _games_by_title dict.
        """
        self._games_by_title.clear()

        if not isinstance(all_entries_raw, list):
            print(
                f"Error: Initial data fetch did not return a list. Cache will be empty. (Received: {type(all_entries_raw)})")
            return

        for entry in all_entries_raw:
            title = entry.get("title")
            if title:
                self._games_by_title[title].append(entry)

    def refresh_cache(self):
        """
        Fetches the full list from the API and rebuilds the local title cache.
        """
        print("Refreshing UmuDatabase cache...")
        all_entries_raw = self.list_all()
        self._build_title_cache(all_entries_raw)
        print("Cache refresh complete.")

    def _request_umu_api(self, params=None):
        """
        Helper function to make a GET request and parse the JSON response.
        """
        response = None
        try:
            response = requests.get(self.umu_api_url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Could not get umu database result for params {params}: {str(e)}")
            return {}
        except requests.exceptions.JSONDecodeError as e:
            if response:
                print(f"Could not decode JSON for params {params} (Response: {response.text}): {str(e)}")
            else:
                print(f"Could not decode JSON for params {params}: {str(e)}")
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