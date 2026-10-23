"""Centralized constants for the Gameyfin application."""

import sys

# Application version — bump on each release
APP_VERSION = "2.14.0"

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

# Freedesktop runtime the Flatpak's KDE runtime is based on (see the manifest)
FDO_RUNTIME_VERSION = "25.08"

# Number of fixed tabs (Main, Downloads, Prefixes, Proton, Settings, System);
# the Proton tab only exists on Linux
FIXED_TAB_COUNT = 6 if sys.platform == "linux" else 5

# Download chunk size for streaming (128 KB)
DOWNLOAD_CHUNK_SIZE = 131072

# Game download timeouts (seconds): (connect, read). The read timeout is the
# longest gap between received bytes; big games behind a proxy can stall for a while.
DOWNLOAD_TIMEOUT = (15, 300)

# How long a stopped game gets to exit after SIGTERM before it is SIGKILLed (ms)
STOP_GAME_GRACE_MS = 5000

# Progress signal interval (seconds)
PROGRESS_SIGNAL_INTERVAL = 0.1

# UI colors (named for maintainability)
COLOR_STATUS_DOWNLOADING = "#3498DB"
COLOR_STATUS_INSTALLING = "#E67E22"

# --- Native library UI (Gameyfin server API) ---
# Vaadin Hilla RPC prefix: POST <prefix>/<Endpoint>/<method>
HILLA_PREFIX = "/connect"

# Game IDs per GameEndpoint.getByIds request when loading full game details
GAME_DETAILS_BATCH_SIZE = 200

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

# Bookshelf library view (widgets/shelf.py). Games stand spine out; a box is
# SHELF_SPINE_MIN..SHELF_SPINE_MAX pixels thick (fixed per game). The game under
# the mouse (or the selected one) slides out SHELF_PULL pixels and turns
# SHELF_TILT degrees over SHELF_SLIDE_MS, showing its front (at most
# SHELF_BOX_WIDTH x SHELF_BOX_HEIGHT) with SHELF_BOX_GAP of room either side.
# Above every row is SHELF_HEADROOM, below it a plank front of
# SHELF_PLANK_HEIGHT carrying the caption; SHELF_FRAME is the case's boards.
SHELF_SPINE_MIN = 24
SHELF_SPINE_MAX = 36
SHELF_BOX_WIDTH = 132
SHELF_BOX_HEIGHT = 190
SHELF_TILT = 38
SHELF_PULL = 8
SHELF_SLIDE_MS = 160
# How long the mouse rests on a box before it slides out
SHELF_HOVER_DELAY_MS = 200
SHELF_BOX_GAP = 8
SHELF_HEADROOM = 26
SHELF_PLANK_HEIGHT = 30
SHELF_FRAME = 16
# The shelf view's size setting (GF_LIBRARY_SHELF_SCALE), in percent of the
# sizes above: bigger boxes for large or high-resolution screens
SHELF_SCALE_DEFAULT = 100
SHELF_SCALE_MIN = 50
SHELF_SCALE_MAX = 300

# Games shown per page in the native library grid. The server returns every
# game in one call, so this is a purely client-side slice of the result set.
LIBRARY_PAGE_SIZE = 100

# Header banner height in the native detail view
HEADER_BANNER_HEIGHT = 220

# Screenshot thumbnail height in the native detail view
SCREENSHOT_THUMB_HEIGHT = 130

# 3D "big box" detail view (widgets/game_box.py)
# Box thickness relative to its width (PC big boxes are chunky)
BOX_DEPTH_RATIO = 0.22
# Front face height / width when the game has no cover to take it from
BOX_DEFAULT_ASPECT = 1.4
# Resting angles in degrees: turned a little so a spine and the top show
BOX_REST_YAW = 24.0
BOX_REST_PITCH = 9.0
BOX_FLIP_MS = 650
# The front cover opens like a book: how far it swings (degrees), how long that
# takes, and the angle the open book is shown at
BOX_OPEN_ANGLE = 168.0
BOX_OPEN_MS = 950
BOX_OPEN_YAW = 6.0
BOX_OPEN_PITCH = 5.0
# Turned further than this (degrees) from the front, the box turns round
# closed before the cover opens
BOX_OPEN_TURN_FIRST = 45.0
# Length of the "disc goes into the drive" animation played when a download
# starts from the box view (the cover-opening part takes ~BOX_OPEN_MS of it)
BOX_DISC_MS = 4300
BOX_DISC_TEXTURE_SIZE = 512
# Pixel width the generated back cover is rendered at
BOX_BACK_TEXTURE_WIDTH = 760
# Width of the title/buttons column beside the box
BOX_SIDE_PANEL_WIDTH = 340

# Subdirectory (under the app data dir) holding cached artwork
IMAGE_CACHE_DIR = "image_cache"

# Concurrent artwork downloads
IMAGE_FETCH_THREADS = 6

# How often to re-check whether the web view's session can drive the API yet.
# Gameyfin finishes login through client-side routing, so there is no page-load
# signal to hook — the API answer itself is the only reliable authentication test.
NATIVE_UI_PROBE_INTERVAL_MS = 3000

# Offline mode: how often to check whether an unreachable server is back, and
# how long that check may take. The check is a plain GET of the server's index.
OFFLINE_RETRY_INTERVAL_MS = 30000
OFFLINE_PING_TIMEOUT = 5

# Debounce for the cookie-driven probe: a login writes several cookies at once
NATIVE_UI_COOKIE_DEBOUNCE_MS = 700
