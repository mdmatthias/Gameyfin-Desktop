"""Centralized constants for the Gameyfin application."""

# Proton version used when no user preference is set
DEFAULT_PROTON = "GE-Proton"

# UMU launcher command name
UMU_RUN_CMD = "umu-run"

# File permission for generated scripts and shortcuts (rwxr-xr-x)
SCRIPT_PERMISSION = 0o755

# Flatpak application ID
FLATPAK_ID = "org.gameyfin.Gameyfin-Desktop"

# Number of fixed tabs (Main, Downloads, Prefixes, Settings)
FIXED_TAB_COUNT = 4

# Download chunk size for streaming (128 KB)
DOWNLOAD_CHUNK_SIZE = 131072

# Progress signal interval (seconds)
PROGRESS_SIGNAL_INTERVAL = 0.1

# UI colors (named for maintainability)
COLOR_STATUS_DOWNLOADING = "#3498DB"
COLOR_STATUS_INSTALLING = "#E67E22"
