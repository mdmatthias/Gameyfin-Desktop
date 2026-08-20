"""Centralized constants for the Gameyfin application."""

# Application version — bump on each release
APP_VERSION = "2.9.9"

# GitHub repository used for update checks
GITHUB_REPO = "mdmatthias/Gameyfin-Desktop"
GITHUB_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

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

# --- Native library UI (Gameyfin server API) ---
# Vaadin Hilla RPC prefix: POST <prefix>/<Endpoint>/<method>
HILLA_PREFIX = "/connect"

# REST artwork paths keyed by ImageDto.type as returned by the server
IMAGE_PATHS = {
    "COVER": "/images/cover",
    "HEADER": "/images/header",
    "SCREENSHOT": "/images/screenshot",
}

# Game download path template (query param: provider=<key>)
DOWNLOAD_PATH = "/download/{game_id}"

# HTTP timeout for API calls (seconds)
API_TIMEOUT = 20

# CSRF cookie/header names used by Hilla (Spring variant first, Vaadin fallback)
SPRING_CSRF_COOKIE = "XSRF-TOKEN"
SPRING_CSRF_HEADER = "X-XSRF-TOKEN"
VAADIN_CSRF_COOKIE = "csrfToken"
VAADIN_CSRF_HEADER = "X-CSRF-Token"

# Cover tile size in the native library grid
COVER_TILE_WIDTH = 180
COVER_TILE_HEIGHT = 270

# Header banner height in the native detail view
HEADER_BANNER_HEIGHT = 220

# Screenshot thumbnail height in the native detail view
SCREENSHOT_THUMB_HEIGHT = 130

# Subdirectory (under the app data dir) holding cached artwork
IMAGE_CACHE_DIR = "image_cache"

# Concurrent artwork downloads
IMAGE_FETCH_THREADS = 6

# How often to re-check whether the web view's session can drive the API yet.
# Gameyfin finishes login through client-side routing, so there is no page-load
# signal to hook — the API answer itself is the only reliable authentication test.
NATIVE_UI_PROBE_INTERVAL_MS = 3000

# Debounce for the cookie-driven probe: a login writes several cookies at once
NATIVE_UI_COOKIE_DEBOUNCE_MS = 700
