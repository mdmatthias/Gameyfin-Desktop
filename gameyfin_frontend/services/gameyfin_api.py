"""Client for the Gameyfin server API.

The Gameyfin server exposes two kinds of HTTP interface:

* **Vaadin Hilla RPC** — ``POST /connect/<Endpoint>/<method>`` with a JSON
  object of named parameters as the body. Used for libraries, games and
  download providers.
* **Plain REST** — ``GET /images/<type>/<id>`` for artwork and
  ``GET /download/<gameId>?provider=<key>`` for game downloads.

Authentication is session-cookie based and this client never logs in itself —
the embedded web view performs the (possibly SSO) login.

Two transports are supported for the RPC calls:

* **In-page** (preferred): a :class:`WebViewRpc` runs the request inside the
  logged-in page, so the browser attaches exactly the credentials and CSRF token
  the working web app uses. Servers reject a mirrored cookie jar in several ways
  — a scoped session cookie, or a CSRF token that only exists in the document —
  and this transport is immune to all of them.
* **Direct HTTP**: a ``requests`` session using cookies from
  ``cookie_provider`` plus a CSRF header derived the way Hilla derives it
  (Spring ``XSRF-TOKEN`` cookie or ``_csrf`` meta, else Vaadin ``csrfToken``
  cookie or the token in ``window.Vaadin.TypeScript``). Used for artwork, and as
  a fallback when no page is available.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

import requests

from ..config import (API_TIMEOUT, DOWNLOAD_PATH, HILLA_PREFIX, IMAGE_PATHS,
                      SPRING_CSRF_COOKIE, SPRING_CSRF_HEADER,
                      VAADIN_CSRF_COOKIE, VAADIN_CSRF_HEADER)
from ..settings import SettingsManager
from .webview_rpc import WebViewRpcError

logger = logging.getLogger(__name__)

_CSRF_HEADER_META = re.compile(
    r"""<meta\s+name=["']_csrf_header["']\s+content=["']([^"']+)["']""", re.IGNORECASE
)
_CSRF_TOKEN_META = re.compile(
    r"""<meta\s+name=["']_csrf["']\s+content=["']([^"']+)["']""", re.IGNORECASE
)
_VAADIN_TOKEN = re.compile(r"""["']csrfToken["']\s*:\s*["']([^"']+)["']""")


class GameyfinApiError(Exception):
    """Raised when a Gameyfin API call fails."""


class GameyfinAuthError(GameyfinApiError):
    """Raised when the server rejects the call as unauthenticated."""


@dataclass(frozen=True)
class GameImage:
    """An ``ImageDto`` from the server."""

    id: int
    type: str
    blurhash: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> "GameImage | None":
        if not data or data.get("id") is None:
            return None
        return cls(id=int(data["id"]), type=str(data.get("type") or "COVER"),
                   blurhash=data.get("blurhash"))


@dataclass(frozen=True)
class Library:
    """A ``LibraryDto`` from the server."""

    id: int
    name: str
    game_ids: list[int] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Library":
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or f"Library {data['id']}"),
            game_ids=[int(g) for g in (data.get("gameIds") or [])],
        )


@dataclass(frozen=True)
class DownloadProvider:
    """A ``DownloadProviderDto`` from the server."""

    key: str
    name: str
    priority: int = 0
    description: str = ""
    short_description: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DownloadProvider":
        return cls(
            key=str(data["key"]),
            name=str(data.get("name") or data["key"]),
            priority=int(data.get("priority") or 0),
            description=str(data.get("description") or ""),
            short_description=data.get("shortDescription"),
        )


@dataclass(frozen=True)
class Game:
    """A ``GameDto`` from the server, flattened for UI use."""

    id: int
    title: str
    library_id: int
    summary: str | None = None
    release: str | None = None
    file_size: int = 0
    user_rating: int | None = None
    critic_rating: int | None = None
    platforms: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    collection_ids: list[int] = field(default_factory=list)
    cover: GameImage | None = None
    header: GameImage | None = None
    images: list[GameImage] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Game":
        metadata = data.get("metadata") or {}
        return cls(
            id=int(data["id"]),
            title=str(data.get("title") or "Unknown"),
            library_id=int(data.get("libraryId") or 0),
            summary=data.get("summary"),
            release=data.get("release"),
            file_size=int(metadata.get("fileSize") or 0),
            user_rating=_to_int(data.get("userRating")),
            critic_rating=_to_int(data.get("criticRating")),
            platforms=_str_list(data.get("platforms")),
            genres=_str_list(data.get("genres")),
            themes=_str_list(data.get("themes")),
            publishers=_str_list(data.get("publishers")),
            developers=_str_list(data.get("developers")),
            features=_str_list(data.get("features")),
            keywords=_str_list(data.get("keywords")),
            collection_ids=[int(c) for c in (data.get("collectionIds") or [])],
            cover=GameImage.from_json(data.get("cover")),
            header=GameImage.from_json(data.get("header")),
            images=[img for img in (GameImage.from_json(i) for i in (data.get("images") or [])) if img],
            video_urls=_str_list(data.get("videoUrls")),
        )


def _to_int(value: Any) -> int | None:
    """Coerce a rating-like value to int, returning None when absent or junk."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_list(value: Any) -> list[str]:
    """Normalise a server list field to a list of display strings."""
    if not value:
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            # Enum DTOs carry a displayName alongside the raw constant
            result.append(str(item.get("displayName") or item.get("name") or item))
        else:
            result.append(str(item))
    return result


class GameyfinApiClient:
    """Talks to the Gameyfin server over Hilla RPC and its REST image/download routes."""

    def __init__(self, settings: SettingsManager,
                 cookie_provider: Callable[[], dict[str, str]] | None = None,
                 session: requests.Session | None = None,
                 rpc_transport: Any | None = None) -> None:
        """Create a client.

        Args:
            settings: Provides ``GF_URL``.
            cookie_provider: Callable returning the current session cookies
                (harvested from the embedded web view). Called before every
                request so a re-login is picked up without recreating the client.
            session: Optional pre-built requests session (used by tests).
            rpc_transport: Optional :class:`~gameyfin_frontend.services.webview_rpc.WebViewRpc`
                that runs endpoint calls inside the logged-in page. Used for every
                RPC call while it reports a page as available.
        """
        self.settings = settings
        self.cookie_provider = cookie_provider or (lambda: {})
        self._session = session or requests.Session()
        self.rpc_transport = rpc_transport
        self._spring_csrf_header: str | None = None
        self._page_csrf: dict[str, str] | None = None

    @property
    def base_url(self) -> str:
        """Return the configured Gameyfin base URL without a trailing slash."""
        return str(self.settings.get("GF_URL") or "").rstrip("/")

    # ------------------------------------------------------------------
    # Cookies / CSRF
    # ------------------------------------------------------------------

    def _cookies(self) -> dict[str, str]:
        """Return the current cookie jar contents from the web view."""
        cookies = self.cookie_provider() or {}
        return {str(k): str(v) for k, v in cookies.items()}

    def _csrf_headers(self, cookies: dict[str, str]) -> dict[str, str]:
        """Build the CSRF header Hilla expects for the current session.

        Mirrors Hilla's own resolution order: a Spring token wins (from the
        ``XSRF-TOKEN`` cookie, else the ``_csrf`` meta tag, with the header name
        taken from ``_csrf_header``), otherwise the Vaadin token is used (from the
        ``csrfToken`` cookie, else the token embedded in the index page).
        """
        page = self._page_csrf_info(cookies)

        spring_token = cookies.get(SPRING_CSRF_COOKIE) or page.get("spring_token")
        if spring_token:
            header = page.get("spring_header") or SPRING_CSRF_HEADER
            logger.debug("Using Spring CSRF token with header %s", header)
            return {header: spring_token}

        vaadin_token = cookies.get(VAADIN_CSRF_COOKIE) or page.get("vaadin_token")
        if vaadin_token:
            logger.debug("Using Vaadin CSRF token")
            return {VAADIN_CSRF_HEADER: vaadin_token}

        logger.debug("No CSRF token found; sending the call without one")
        return {}

    def _page_csrf_info(self, cookies: dict[str, str]) -> dict[str, str]:
        """Return CSRF details scraped from the index page, fetching it once.

        Spring's cookie repository puts the token in a cookie and only the header
        *name* in the page, while its session repository puts the token in the page
        too — so header name and token are looked up independently.
        """
        if self._page_csrf is not None:
            return self._page_csrf

        info: dict[str, str] = {}
        try:
            response = self._session.get(f"{self.base_url}/", cookies=cookies, timeout=API_TIMEOUT)
            body = response.text if isinstance(response.text, str) else ""
        except requests.RequestException as e:
            logger.debug("Could not read CSRF info from the index page: %s", e)
            body = ""

        header = _CSRF_HEADER_META.search(body)
        if header:
            info["spring_header"] = header.group(1)
        spring_token = _CSRF_TOKEN_META.search(body)
        if spring_token:
            info["spring_token"] = spring_token.group(1)
        vaadin_token = _VAADIN_TOKEN.search(body)
        if vaadin_token:
            info["vaadin_token"] = vaadin_token.group(1)

        # Cached even when empty so the index page is not re-fetched per call
        self._page_csrf = info
        return info

    def reset_csrf(self) -> None:
        """Forget cached CSRF info (call after a server URL change or a 401)."""
        self._spring_csrf_header = None
        self._page_csrf = None

    # ------------------------------------------------------------------
    # Hilla RPC
    # ------------------------------------------------------------------

    def call(self, endpoint: str, method: str, params: dict[str, Any] | None = None) -> Any:
        """Invoke a Hilla endpoint method and return its decoded JSON result.

        Runs inside the logged-in page when a web view transport is available,
        falling back to a direct HTTP request otherwise.

        Args:
            endpoint: Endpoint class name, e.g. ``GameEndpoint``.
            method: Method name, e.g. ``getAll``.
            params: Named parameters for the method.

        Raises:
            GameyfinAuthError: The server answered 401/403.
            GameyfinApiError: Any other transport or decoding failure.
        """
        if not self.base_url:
            raise GameyfinApiError("No Gameyfin URL configured")

        transport = self.rpc_transport
        if transport is not None:
            try:
                if transport.available():
                    payload = transport.call(endpoint, method, params)
                    return self._interpret(
                        endpoint, method, int(payload.get("status") or 0),
                        str(payload.get("body") or ""),
                    )
                logger.debug("No web view page yet; falling back to a direct request")
            except (WebViewRpcError, RuntimeError) as e:
                # No page, a timeout, or a Qt teardown race — a direct request is
                # still worth trying before giving up on this refresh.
                logger.debug("Web view transport unusable (%s); trying a direct request", e)

        return self._call_over_http(endpoint, method, params)

    def _call_over_http(self, endpoint: str, method: str,
                        params: dict[str, Any] | None) -> Any:
        """Invoke an endpoint with a direct HTTP request, retrying once on 401/403.

        A rejected call is usually a stale CSRF token, which is worth re-reading
        once before reporting the session as unauthenticated.
        """
        url = f"{self.base_url}{HILLA_PREFIX}/{endpoint}/{method}"

        for attempt in (1, 2):
            cookies = self._cookies()
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            headers.update(self._csrf_headers(cookies))

            try:
                response = self._session.post(
                    url, json=params or {}, headers=headers, cookies=cookies,
                    timeout=API_TIMEOUT,
                )
            except requests.RequestException as e:
                raise GameyfinApiError(f"Could not reach {url}: {e}") from e

            if response.status_code in (401, 403) and attempt == 1:
                logger.debug(
                    "%s.%s rejected with HTTP %s (body: %.200s); refreshing CSRF info",
                    endpoint, method, response.status_code, response.text,
                )
                self.reset_csrf()
                continue

            return self._interpret(endpoint, method, response.status_code, response.text)

        raise GameyfinApiError(f"{endpoint}.{method} failed")

    def _interpret(self, endpoint: str, method: str, status: int, body: str) -> Any:
        """Turn a transport-agnostic (status, body) pair into a result or an error."""
        if status == 0:
            raise GameyfinApiError(f"{endpoint}.{method} did not reach the server")
        if status in (401, 403):
            logger.debug("%s.%s rejected with HTTP %s (body: %.200s)",
                         endpoint, method, status, body)
            raise GameyfinAuthError(f"Not authenticated for {endpoint}.{method}")
        if status >= 400:
            raise GameyfinApiError(
                f"{endpoint}.{method} failed with HTTP {status}: {body[:200]}"
            )

        if not body:
            return None

        try:
            return json.loads(body)
        except ValueError as e:
            # An HTML body here almost always means the login page was served
            if "<html" in body[:200].lower():
                raise GameyfinAuthError(f"{endpoint}.{method} returned a login page") from e
            raise GameyfinApiError(f"{endpoint}.{method} returned invalid JSON: {e}") from e

    def get_libraries(self) -> list[Library]:
        """Return all libraries visible to the logged-in user."""
        data = self.call("LibraryEndpoint", "getAll") or []
        return [Library.from_json(item) for item in data]

    def get_games(self) -> list[Game]:
        """Return all games visible to the logged-in user."""
        data = self.call("GameEndpoint", "getAll") or []
        return [Game.from_json(item) for item in data]

    def get_download_providers(self) -> list[DownloadProvider]:
        """Return the server's download providers, highest priority first."""
        data = self.call("DownloadProviderEndpoint", "getProviders") or []
        providers = [DownloadProvider.from_json(item) for item in data]
        return sorted(providers, key=lambda p: p.priority, reverse=True)

    # ------------------------------------------------------------------
    # REST routes
    # ------------------------------------------------------------------

    def image_url(self, image: GameImage) -> str:
        """Return the REST URL serving *image*."""
        path = IMAGE_PATHS.get(image.type.upper(), IMAGE_PATHS["COVER"])
        return f"{self.base_url}{path}/{image.id}"

    def download_url(self, game_id: int, provider_key: str) -> str:
        """Return the download URL for *game_id* served by *provider_key*."""
        path = DOWNLOAD_PATH.format(game_id=game_id)
        return f"{self.base_url}{path}?provider={quote(provider_key, safe='')}"

    def fetch_image(self, image: GameImage) -> bytes:
        """Download the bytes of *image*.

        Raises:
            GameyfinApiError: The image could not be fetched.
        """
        url = self.image_url(image)
        try:
            response = self._session.get(url, cookies=self._cookies(), timeout=API_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as e:
            raise GameyfinApiError(f"Could not fetch image {image.id}: {e}") from e
        return response.content
