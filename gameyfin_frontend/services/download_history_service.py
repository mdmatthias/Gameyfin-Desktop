"""Persist and manage download history as JSON."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class DownloadHistoryService:
    """Handles loading, saving, and querying download history from JSON."""

    def __init__(self, json_path: str) -> None:
        """Initialize the download history service.

        Args:
            json_path: File path to the JSON history file.
        """
        self.json_path = json_path

    def load(self) -> list[dict[str, Any]]:
        """Load persisted download history from JSON.

        Marks any records with status "Downloading" as "Failed" since
        incomplete downloads should be restarted.

        Returns:
            List of download record dicts, or empty list on error.
        """
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    records = json.load(f)

                # Mark incomplete downloads as failed
                for record in records:
                    if record.get("status") == "Downloading":
                        record["status"] = "Failed"

                return records
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error loading download history: %s", e)

        return []

    def save(self, records: list[dict[str, Any]]) -> None:
        """Persist download records to JSON.

        Args:
            records: List of download record dicts to serialize.
        """
        try:
            with open(self.json_path, 'w') as f:
                json.dump(records, f, indent=4)
        except OSError as e:
            logger.error("Error saving download history: %s", e)

    def find_by_url(self, records: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
        """Find a download record by its URL.

        Args:
            records: List of download records to search.
            url: URL to match.

        Returns:
            Matching record dict, or None if not found.
        """
        for record in records:
            if record.get("url") == url:
                return record
        return None
