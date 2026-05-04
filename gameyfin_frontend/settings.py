import json
import logging
import os
from typing import Any

from PyQt6.QtCore import QStandardPaths

logger = logging.getLogger(__name__)

class SettingsManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SettingsManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.settings_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        os.makedirs(self.settings_dir, exist_ok=True)
        self.settings_file = os.path.join(self.settings_dir, "settings.json")
        
        # Legacy config directory for backward compatibility
        self.legacy_config_dir = os.path.join(
            os.path.expanduser("~"), ".config", "gameyfin"
        )

        self.defaults = {
            "GF_URL": "http://localhost:8080",
            "GF_WINDOW_WIDTH": 1420,
            "GF_WINDOW_HEIGHT": 940,
            "GF_START_MINIMIZED": 0,
            "GF_ICON_PATH": "",
            "PROTONPATH": "GE-Proton",
            "GF_UMU_API_URL": "https://umu.openwinecomponents.org/umu_api.php",
            "GF_UMU_DB_STORES": ["none", "gog", "amazon", "battlenet", "ea", "egs", "epic", "humble", "itchio", "origin", "steam", "uplay", "ubisoft"],
            "GF_THEME": "auto",
            "GF_DEFAULT_DOWNLOAD_DIR": "",
            "GF_PROMPT_DOWNLOAD_DIR": 0,
            "GF_LOG_LEVEL": "WARNING"
        }
        
        self.settings = self.defaults.copy()
        self.load()
        self._initialized = True

    def load(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    loaded_settings = json.load(f)
                    self.settings.update(loaded_settings)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Error loading settings: %s", e)

    def save(self):
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except OSError as e:
            logger.error("Error saving settings: %s", e)

    def get(self, key: str, fallback: Any = None) -> Any:
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
        self.settings[key] = value
        self.save()

    def get_config_dir(self) -> str:
        return self.settings_dir

    def get_prefixes_dirs(self) -> list[str]:
        """Return list of prefix directories to scan (new + legacy for backward compat)."""
        dirs = [os.path.join(self.settings_dir, "prefixes")]
        legacy = os.path.join(self.legacy_config_dir, "prefixes")
        if os.path.exists(legacy):
            dirs.append(legacy)
        return dirs

    def get_prefixes_dir(self) -> str:
        """Return the new (primary) prefix directory for creating new prefixes."""
        return os.path.join(self.settings_dir, "prefixes")

    def get_shortcuts_dirs(self, game_name: str) -> list[str]:
        """Return list of shortcut script dirs to scan (new + legacy for backward compat)."""
        dirs = [os.path.join(self.settings_dir, "shortcut_scripts", game_name)]
        legacy = os.path.join(self.legacy_config_dir, "shortcut_scripts", game_name)
        if os.path.exists(legacy):
            dirs.append(legacy)
        return dirs

    def get_shortcuts_dir(self, game_name: str) -> str:
        """Return the new (primary) shortcut dir for creating new scripts."""
        return os.path.join(self.settings_dir, "shortcut_scripts", game_name)

    def get_downloads_json_path(self) -> str:
        return os.path.join(self.settings_dir, "downloads.json")

    def get_umu_cache_path(self) -> str:
        return os.path.join(self.settings_dir, "umu_cache.json")

# Global instance
settings_manager = SettingsManager()
