import json
import logging
import os
from typing import Any

from PyQt6.QtCore import QStandardPaths

from gameyfin_frontend.config import DEFAULT_PROTON
from gameyfin_frontend.utils import sanitize_name

logger = logging.getLogger(__name__)

class SettingsManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SettingsManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> "SettingsManager":
        """Return the singleton SettingsManager instance.

        Use this at the top level to wire settings into components that
        accept it as a constructor parameter.
        """
        if cls._instance is None:
            cls()
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.settings_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        os.makedirs(self.settings_dir, exist_ok=True)
        self.settings_file = os.path.join(self.settings_dir, "settings.json")
        

        self.defaults = {
            "GF_URL": "http://localhost:8080",
            "GF_WINDOW_WIDTH": 1420,
            "GF_WINDOW_HEIGHT": 940,
            "GF_START_MINIMIZED": 0,
            "GF_ICON_PATH": "",
            "PROTONPATH": DEFAULT_PROTON,
            "GF_UMU_API_URL": "https://umu.openwinecomponents.org/umu_api.php",
            "GF_UMU_DB_STORES": ["none", "gog", "amazon", "battlenet", "ea", "egs", "epic", "humble", "itchio", "origin", "steam", "uplay", "ubisoft"],
            "GF_THEME": "auto",
            "GF_DEFAULT_DOWNLOAD_DIR": "",
            "GF_PROMPT_DOWNLOAD_DIR": 0,
            "GF_LOG_LEVEL": "WARNING",
            "GF_DOWNLOAD_NOTIFICATIONS": 1,
            "GF_BANDWIDTH_LIMIT": 0,
            # Native Qt library UI instead of the embedded web view (experimental)
            "GF_NATIVE_UI": 0,
            # Games per page in the native library grid (client-side paging)
            "GF_LIBRARY_PAGE_SIZE": 100,
            "GF_GAMEPAD_ENABLED": 1,
            "GF_GAMEPAD_HINTS": 1,
            # Stick deadzone in percent of full deflection
            "GF_GAMEPAD_DEADZONE": 25,
            # Milliseconds between repeats while a direction is held
            "GF_GAMEPAD_REPEAT_MS": 140,
            # Right-stick scroll speed and virtual-mouse speed, in pixels
            "GF_GAMEPAD_SCROLL_SPEED": 60,
        }
        
        self.settings = self.defaults.copy()
        self.load()
        self._initialized = True

    def load(self) -> None:
        """Load settings from the JSON file on disk, merging into current state."""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    loaded_settings = json.load(f)
                    self.settings.update(loaded_settings)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Error loading settings: %s", e)

    def save(self) -> None:
        """Persist current settings dict to the JSON file on disk."""
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except OSError as e:
            logger.error("Error saving settings: %s", e)

    def get(self, key: str, fallback: Any = None) -> Any:
        """Return the value for *key*, falling back to the default, then env var, then fallback arg.

        Resolution order: in-memory setting → defaults dict → environment variable → *fallback* arg.
        """
        # Allow override by environment variables for backward compatibility/debugging
        env_val = os.getenv(key)
        if env_val is not None:
            # Try to convert to int if it looks like one and default is int
            if isinstance(self.defaults.get(key), int):
                try:
                    return int(env_val)
                except ValueError:
                    pass
            return env_val
        
        val = self.settings.get(key)
        if (val is None or val == "") and fallback:
            return fallback
            
        return val if val is not None else self.defaults.get(key)

    def set(self, key: str, value: Any) -> None:
        """Set *key* to *value* in the settings dict and persist to disk."""
        self.settings[key] = value
        self.save()

    def get_config_dir(self) -> str:
        """Return the absolute path to the settings directory."""
        return self.settings_dir

    def get_prefixes_dirs(self) -> list[str]:
        """Return directories to scan for existing Wine prefixes.

        Includes both the new primary location and the legacy location
        (~/.config/gameyfin/prefixes/) so existing prefixes are still
        discovered even though they're not auto-migrated. New prefixes
        are always created in the new location only (see get_prefixes_dir).
        """
        dirs = [os.path.join(self.settings_dir, "prefixes")]
        legacy_prefixes = os.path.join(
            os.path.expanduser("~"), ".config", "gameyfin", "prefixes"
        )
        if os.path.isdir(legacy_prefixes):
            dirs.append(legacy_prefixes)
        return dirs

    def get_prefixes_dir(self) -> str:
        """Return the new (primary) prefix directory for creating new prefixes."""
        return os.path.join(self.settings_dir, "prefixes")

    def get_shortcuts_dirs(self, game_name: str) -> list[str]:
        return [os.path.join(self.settings_dir, "shortcut_scripts", sanitize_name(game_name))]

    def get_shortcuts_dir(self, game_name: str) -> str:
        """Return the new (primary) shortcut dir for creating new scripts."""
        return os.path.join(self.settings_dir, "shortcut_scripts", sanitize_name(game_name))

    def get_downloads_json_path(self) -> str:
        """Return the path to the downloads history JSON file."""
        return os.path.join(self.settings_dir, "downloads.json")

    def get_umu_cache_path(self) -> str:
        """Return the path to the UMU game database cache JSON file."""
        return os.path.join(self.settings_dir, "umu_cache.json")

# Global instance
settings_manager = SettingsManager()
