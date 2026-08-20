"""End-to-end test of the native library UI against a stub Gameyfin server.

This exercises the real HTTP layer — Hilla's ``POST /connect/<Endpoint>/<method>``
convention, the CSRF header, the ``/images/...`` routes and the download URL —
through the real client, image cache and library browser widget.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gameyfin_frontend.services.gameyfin_api import GameyfinApiClient
from gameyfin_frontend.services.image_cache import ImageCache
from gameyfin_frontend.widgets.library_browser import LibraryBrowserWidget

LIBRARIES = [{"id": 1, "name": "PC Games", "gameIds": [1, 2]}]
GAMES = [
    {
        "id": 1, "libraryId": 1, "title": "Alpha", "summary": "First",
        "release": "1999-05-05", "platforms": ["WINDOWS"],
        "genres": [{"name": "SHOOTER", "displayName": "Shooter"}],
        "cover": {"id": 100, "type": "COVER", "blurhash": None},
        "images": [], "metadata": {"fileSize": 1048576},
    },
    {
        "id": 2, "libraryId": 1, "title": "Beta", "summary": None,
        "cover": None, "images": [{"id": 101, "type": "SCREENSHOT"}],
        "metadata": {"fileSize": 0},
    },
]
PROVIDERS = [{"key": "fs", "name": "Filesystem", "priority": 10, "description": "d"}]
COVER_BYTES = b"\x89PNG\r\n\x1a\nnot-a-real-image"


class _Handler(BaseHTTPRequestHandler):
    """Serves just enough of the Gameyfin API for the client to work against."""

    calls: list[tuple[str, dict]] = []

    def log_message(self, *args):  # noqa: D102 - silence the default stderr logging
        pass

    def _send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = b'<html><head><meta name="_csrf_header" content="X-XSRF-TOKEN"></head></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/images/cover/"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(COVER_BYTES)))
            self.end_headers()
            self.wfile.write(COVER_BYTES)
            return

        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        _Handler.calls.append((self.path, dict(self.headers)))

        if not self.headers.get("X-CSRF-Token"):
            self.send_error(403)
            return

        routes = {
            "/connect/LibraryEndpoint/getAll": LIBRARIES,
            "/connect/GameEndpoint/getAll": GAMES,
            "/connect/DownloadProviderEndpoint/getProviders": PROVIDERS,
        }
        if self.path in routes:
            assert json.loads(raw) == {}
            self._send_json(routes[self.path])
            return

        self.send_error(404)


@pytest.fixture()
def stub_server():
    """Run the stub Gameyfin server on a random port for one test."""
    _Handler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def api_settings(stub_server, tmp_path):
    """Settings pointing at the stub server with a temp config dir."""
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: (
        stub_server if key == "GF_URL" else default
    )
    settings.get_config_dir.return_value = str(tmp_path)
    return settings


def test_library_loads_over_http(qtbot, api_settings):
    """The grid fills from real HTTP responses and the cover lands on disk."""
    client = GameyfinApiClient(api_settings, cookie_provider=lambda: {"csrfToken": "tok"})
    cache = ImageCache(client, api_settings)
    browser = LibraryBrowserWidget(client, cache, api_settings)
    qtbot.addWidget(browser)

    browser.refresh()
    qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=10000)

    assert [browser.grid.item(i).text() for i in range(2)] == ["Alpha", "Beta"]
    assert browser.games[0].file_size == 1048576
    assert browser.games[0].genres == ["Shooter"]
    assert [p.key for p in browser.providers] == ["fs"]

    # All three endpoints were reached with the Hilla POST convention
    paths = [path for path, _ in _Handler.calls]
    assert paths == [
        "/connect/LibraryEndpoint/getAll",
        "/connect/GameEndpoint/getAll",
        "/connect/DownloadProviderEndpoint/getProviders",
    ]

    # The detail page renders and hands back a working download URL
    browser._open_item(browser.grid.item(0))
    assert browser.detail.game.title == "Alpha"
    assert client.download_url(1, browser.detail.selected_provider_key()).endswith(
        "/download/1?provider=fs"
    )


def test_cover_is_fetched_and_cached(qtbot, api_settings):
    """A cover requested by the grid is fetched over HTTP and cached on disk."""
    from gameyfin_frontend.services.gameyfin_api import GameImage

    client = GameyfinApiClient(api_settings, cookie_provider=lambda: {"csrfToken": "tok"})
    cache = ImageCache(client, api_settings)
    image = GameImage(id=100, type="COVER")

    with qtbot.waitSignal(cache.ready, timeout=10000) as blocker:
        assert cache.request(image) is None

    assert blocker.args == [100, COVER_BYTES]
    # Second request is served from disk without touching the network
    assert cache.request(image) == COVER_BYTES


def test_probe_recovers_once_the_session_becomes_valid(qtbot, api_settings, monkeypatch):
    """A 401 refresh reports login_required; a later refresh loads normally.

    This is the login sequence the desktop app sees: the first probe runs before
    the web view has finished authenticating, so it must not be terminal.
    """
    cookies = {}
    client = GameyfinApiClient(api_settings, cookie_provider=lambda: dict(cookies))
    cache = ImageCache(client, api_settings)
    browser = LibraryBrowserWidget(client, cache, api_settings)
    qtbot.addWidget(browser)

    # No CSRF cookie yet -> the stub server answers 403, like a pre-login probe
    with qtbot.waitSignal(browser.login_required, timeout=10000):
        browser.refresh()
    assert browser.grid.count() == 0

    # "Login" completes and the session cookies appear
    cookies["csrfToken"] = "tok"
    with qtbot.waitSignal(browser.library_loaded, timeout=10000):
        browser.refresh()

    assert browser.grid.count() == 2


def test_missing_csrf_header_is_reported_as_auth_error(qtbot, api_settings):
    """Without a CSRF cookie the server rejects the call and the UI says so."""
    client = GameyfinApiClient(api_settings, cookie_provider=lambda: {})
    cache = ImageCache(client, api_settings)
    browser = LibraryBrowserWidget(client, cache, api_settings)
    qtbot.addWidget(browser)

    with qtbot.waitSignal(browser.login_required, timeout=10000):
        browser.refresh()

    assert browser.grid.count() == 0
