import os
import json
from PyQt6.QtCore import QStandardPaths

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
        
        self.defaults = {
            "GF_URL": "http://localhost:8080",
            "GF_WINDOW_WIDTH": 1420,
            "GF_WINDOW_HEIGHT": 940,
            "GF_START_MINIMIZED": 0,
            "GF_ICON_PATH": "",
            "PROTONPATH": "GE-Proton",
            "GF_UMU_API_URL": "https://umu.openwinecomponents.org/umu_api.php",
            "GF_UMU_DB_STORES": ["none", "gog", "amazon", "battlenet", "ea", "egs", "epic", "humble", "itchio", "origin", "steam", "uplay", "ubisoft"],
            "GF_THEME": "auto"
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
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save(self):
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def get(self, key, fallback=None):
        # Allow override by environment variables for backward compatibility/debugging
        env_val = os.getenv(key)
        if env_val is not None:
            # Try to convert to int if it looks like one and default is int
            if isinstance(self.defaults.get(key), int):
                try: return int(env_val)
                except: pass
            return env_val
        
        val = self.settings.get(key)
        if (val is None or val == "") and fallback:
            return fallback
            
        return val if val is not None else self.defaults.get(key)

    def set(self, key, value):
        self.settings[key] = value
        self.save()

# Global instance
settings_manager = SettingsManager()
