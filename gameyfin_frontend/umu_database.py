from typing import List

import requests
from os import getenv


class UmuDatabase:
    def __init__(self):
        self.umu_api_url = getenv("GF_UMU_API_URL", "https://umu.openwinecomponents.org/umu_api.php")

    def _try_request(self, params=None):
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

    def list_all(self):
        """
        List ALL entries
        API: /umu_api.php
        """
        return self._try_request()

    def list_all_by_store(self, store: str):
        """
        List ALL entries based on STORE
        API: /umu_api.php?store=SOME-STORE
        """
        return self._try_request(params={"store": store.lower()})

    def get_title_and_umu_id_by_store_and_codename(self, store: str, codename: str):
        """
        Get TITLE and UMU_ID based on STORE and CODENAME
        API: /umu_api.php?store=SOME-STORE&codename=SOME-CODENAME-OR-APP-ID
        """
        return self._try_request(params={"store": store.lower(), "codename": codename.lower()})

    def get_game_by_codename(self, codename: str) -> List:
        """
        Get ALL GAME VALUES based on CODENAME
        API: /umu_api.php?codename=SOME-CODENAME-OR-APP-ID
        """
        return self._try_request(params={"codename": codename.lower()})

    def get_title_by_store_and_umu_id(self, store: str, umu_id: str):
        """
        Get TITLE based on UMU_ID and STORE
        API: /umu_api.php?umu_id=SOME-UMU-ID&store=SOME-STORE-OR-NONE
        """
        return self._try_request(params={"store": store.lower(), "umu_id": umu_id.lower()})

    def get_game_by_umu_id(self, umu_id: str):
        """
        Get ALL GAME VALUES AND ENTRIES based on UMU_ID
        API: /umu_api.php?umu_id=SOME-UMU-ID
        """
        return self._try_request(params={"umu_id": umu_id.lower()})

    def get_umu_id_by_title_and_store(self, title: str, store: str):
        """
        Get UMU_ID based on TITLE and STORE
        API: /umu_api.php?title=SOME-GAME-TITLE&STORE=SOME-STORE
        (Note: Title is not lowercased as it may be case-sensitive)
        """
        return self._try_request(params={"title": title, "store": store.lower()})

    def get_umu_id_by_title(self, title: str):
        """
        Get UMU_ID based on TITLE and no store
        API: /umu_api.php?title=SOME-GAME-TITLE
        (Note: Title is not lowercased as it may be case-sensitive)
        """
        return self._try_request(params={"title": title})
