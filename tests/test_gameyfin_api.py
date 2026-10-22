"""Tests for the Gameyfin server API client (Hilla RPC + REST routes)."""

import json
from unittest.mock import MagicMock

import pytest
import requests

from gameyfin_frontend.services.gameyfin_api import (DownloadProvider, Game,
                                                     GameImage,
                                                     GameyfinApiClient,
                                                     GameyfinApiError,
                                                     GameyfinAuthError,
                                                     GameyfinConnectionError, Library)


class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code=200, payload=None, text="", content=b"x"):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = json.dumps(payload).encode() if payload is not None else content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture()
def settings():
    """Settings stub exposing only GF_URL."""
    stub = MagicMock()
    stub.get.side_effect = lambda key, fallback=None: (
        "http://gameyfin.test:8080/" if key == "GF_URL" else fallback
    )
    return stub


@pytest.fixture()
def session():
    """A mock requests session."""
    return MagicMock()


@pytest.fixture()
def client(settings, session):
    """Client wired to the mock session with a Vaadin CSRF cookie."""
    return GameyfinApiClient(
        settings, cookie_provider=lambda: {"JSESSIONID": "abc", "csrfToken": "tok"},
        session=session,
    )


class TestUrls:
    def test_base_url_strips_trailing_slash(self, client):
        assert client.base_url == "http://gameyfin.test:8080"

    def test_download_url_encodes_provider(self, client):
        url = client.download_url(7, "file system")
        assert url == "http://gameyfin.test:8080/download/7?provider=file%20system"

    def test_image_url_uses_type_specific_path(self, client):
        assert client.image_url(GameImage(id=3, type="COVER")).endswith("/images/cover/3")
        assert client.image_url(GameImage(id=4, type="HEADER")).endswith("/images/header/4")
        assert client.image_url(GameImage(id=5, type="SCREENSHOT")).endswith("/images/screenshot/5")

    def test_unknown_image_type_falls_back_to_cover(self, client):
        assert client.image_url(GameImage(id=6, type="WEIRD")).endswith("/images/cover/6")


class TestCall:
    def test_posts_to_hilla_endpoint_with_csrf_header(self, client, session):
        session.post.return_value = FakeResponse(payload=[])

        client.call("GameEndpoint", "getAll")

        args, kwargs = session.post.call_args
        assert args[0] == "http://gameyfin.test:8080/connect/GameEndpoint/getAll"
        assert kwargs["json"] == {}
        assert kwargs["headers"]["X-CSRF-Token"] == "tok"
        assert kwargs["cookies"]["JSESSIONID"] == "abc"

    def test_params_are_sent_as_named_json_object(self, client, session):
        session.post.return_value = FakeResponse(payload={"ok": True})

        client.call("GameEndpoint", "deleteGame", {"gameId": 12})

        assert session.post.call_args.kwargs["json"] == {"gameId": 12}

    def test_spring_csrf_cookie_wins_and_uses_meta_header_name(self, settings, session):
        session.get.return_value = FakeResponse(
            text='<meta name="_csrf_header" content="X-Custom-Csrf">'
        )
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(
            settings, cookie_provider=lambda: {"XSRF-TOKEN": "spring-tok"}, session=session
        )

        client.call("LibraryEndpoint", "getAll")

        assert session.post.call_args.kwargs["headers"]["X-Custom-Csrf"] == "spring-tok"

    def test_spring_csrf_header_defaults_when_meta_missing(self, settings, session):
        session.get.return_value = FakeResponse(text="<html><head></head></html>")
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(
            settings, cookie_provider=lambda: {"XSRF-TOKEN": "spring-tok"}, session=session
        )

        client.call("LibraryEndpoint", "getAll")

        assert session.post.call_args.kwargs["headers"]["X-XSRF-TOKEN"] == "spring-tok"

    def test_csrf_token_can_come_from_the_page_when_no_cookie_is_visible(self, settings, session):
        """Spring's session repository exposes the token only in the document."""
        session.get.return_value = FakeResponse(
            text='<meta name="_csrf" content="page-tok">'
                 '<meta name="_csrf_header" content="X-Custom-Csrf">'
        )
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(settings, cookie_provider=lambda: {}, session=session)

        client.call("LibraryEndpoint", "getAll")

        assert session.post.call_args.kwargs["headers"]["X-Custom-Csrf"] == "page-tok"

    def test_vaadin_token_can_come_from_the_page(self, settings, session):
        session.get.return_value = FakeResponse(
            text='<script>window.Vaadin = {TypeScript: {"csrfToken":"vaadin-page-tok"}};</script>'
        )
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(settings, cookie_provider=lambda: {}, session=session)

        client.call("LibraryEndpoint", "getAll")

        assert session.post.call_args.kwargs["headers"]["X-CSRF-Token"] == "vaadin-page-tok"

    def test_rejected_call_refreshes_csrf_info_and_retries_once(self, settings, session):
        session.get.return_value = FakeResponse(text='<meta name="_csrf" content="tok">')
        session.post.side_effect = [FakeResponse(status_code=401), FakeResponse(payload=[])]
        client = GameyfinApiClient(settings, cookie_provider=lambda: {}, session=session)

        assert client.call("LibraryEndpoint", "getAll") == []
        assert session.post.call_count == 2
        # The page was re-read for a fresh token before the retry
        assert session.get.call_count == 2

    def test_still_auth_error_when_the_retry_is_also_rejected(self, settings, session):
        session.get.return_value = FakeResponse(text="<html></html>")
        session.post.return_value = FakeResponse(status_code=401)
        client = GameyfinApiClient(settings, cookie_provider=lambda: {}, session=session)

        with pytest.raises(GameyfinAuthError):
            client.call("LibraryEndpoint", "getAll")

    def test_csrf_header_name_is_only_looked_up_once(self, settings, session):
        session.get.return_value = FakeResponse(text="<html></html>")
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(
            settings, cookie_provider=lambda: {"XSRF-TOKEN": "t"}, session=session
        )

        client.call("LibraryEndpoint", "getAll")
        client.call("GameEndpoint", "getAll")

        assert session.get.call_count == 1

    def test_reset_csrf_forces_a_new_lookup(self, settings, session):
        session.get.return_value = FakeResponse(text="<html></html>")
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(
            settings, cookie_provider=lambda: {"XSRF-TOKEN": "t"}, session=session
        )

        client.call("LibraryEndpoint", "getAll")
        client.reset_csrf()
        client.call("LibraryEndpoint", "getAll")

        assert session.get.call_count == 2

    def test_no_csrf_cookie_sends_no_csrf_header(self, settings, session):
        # Nothing in the cookies and nothing in the page either
        session.get.return_value = FakeResponse(text="<html><head></head></html>")
        session.post.return_value = FakeResponse(payload=[])
        client = GameyfinApiClient(settings, cookie_provider=lambda: {}, session=session)

        client.call("GameEndpoint", "getAll")

        headers = session.post.call_args.kwargs["headers"]
        assert "X-CSRF-Token" not in headers
        assert "X-XSRF-TOKEN" not in headers

    @pytest.mark.parametrize("status", [401, 403])
    def test_unauthenticated_status_raises_auth_error(self, client, session, status):
        session.post.return_value = FakeResponse(status_code=status)

        with pytest.raises(GameyfinAuthError):
            client.call("GameEndpoint", "getAll")

    def test_login_page_body_raises_auth_error(self, client, session):
        session.post.return_value = FakeResponse(text="<html><body>login</body></html>",
                                                 content=b"<html>")

        with pytest.raises(GameyfinAuthError):
            client.call("GameEndpoint", "getAll")

    def test_server_error_raises_api_error(self, client, session):
        session.post.return_value = FakeResponse(status_code=500)

        with pytest.raises(GameyfinApiError):
            client.call("GameEndpoint", "getAll")

    def test_transport_failure_raises_api_error(self, client, session):
        session.post.side_effect = requests.ConnectionError("refused")

        with pytest.raises(GameyfinApiError):
            client.call("GameEndpoint", "getAll")

    def test_transport_failure_is_a_connection_error(self, client, session):
        session.post.side_effect = requests.ConnectionError("refused")

        with pytest.raises(GameyfinConnectionError):
            client.call("GameEndpoint", "getAll")

    @pytest.mark.parametrize("status", [502, 503, 504])
    def test_proxy_without_upstream_is_a_connection_error(self, client, session, status):
        session.post.return_value = FakeResponse(status_code=status)

        with pytest.raises(GameyfinConnectionError):
            client.call("GameEndpoint", "getAll")

    def test_server_error_is_not_a_connection_error(self, client, session):
        session.post.return_value = FakeResponse(status_code=500)

        with pytest.raises(GameyfinApiError) as excinfo:
            client.call("GameEndpoint", "getAll")
        assert not isinstance(excinfo.value, GameyfinConnectionError)

    def test_missing_url_raises_api_error(self, session):
        settings = MagicMock()
        settings.get.return_value = ""
        client = GameyfinApiClient(settings, session=session)

        with pytest.raises(GameyfinApiError):
            client.call("GameEndpoint", "getAll")

    def test_empty_body_returns_none(self, client, session):
        session.post.return_value = FakeResponse(content=b"")

        assert client.call("GameEndpoint", "getAll") is None


def route(session, **responses):
    """Answer session.post per Hilla method name (``sync``, ``getByIds``, ...).

    Values are payloads, FakeResponses, or callables taking the JSON body.
    Unrouted methods answer with Hilla's empty 403.
    """
    def post(url, json=None, **_kwargs):
        method = url.rsplit("/", 1)[-1]
        if method not in responses:
            return FakeResponse(status_code=403, text="")
        value = responses[method]
        if callable(value):
            value = value(json)
        return value if isinstance(value, FakeResponse) else FakeResponse(payload=value)

    session.post.side_effect = post


def called_methods(session):
    return [c.args[0].rsplit("/", 1)[-1] for c in session.post.call_args_list]


class TestTypedCalls:
    def test_get_libraries(self, client, session):
        route(session, sync={"serverTime": "now", "full": True, "allIds": [], "libraries": [
            {"id": 1, "name": "PC", "metadata": {}},
            {"id": 2, "name": None},
        ]})

        libraries = client.get_libraries()

        assert libraries == [
            Library(id=1, name="PC"),
            Library(id=2, name="Library 2"),
        ]
        assert json.loads(json.dumps(session.post.call_args.kwargs["json"])) == {"since": None}

    def test_get_games_flattens_dto(self, client, session):
        route(session, sync={"games": [{"id": 42, "title": "Half-Life", "fileSize": 2048}]},
              getByIds=[{
                  "id": 42,
                  "libraryId": 1,
                  "title": "Half-Life",
                  "summary": "Ride the tram.",
                  "release": "1998-11-19",
                  "userRating": 96,
                  "criticRating": None,
                  "platforms": ["WINDOWS"],
                  "genres": [{"name": "SHOOTER", "displayName": "Shooter"}],
                  "developers": ["Valve"],
                  "cover": {"id": 5, "type": "COVER", "blurhash": "abc"},
                  "header": None,
                  "images": [{"id": 6, "type": "SCREENSHOT"}, None],
                  "metadata": {"fileSize": 2048},
              }])

        game = client.get_games()[0]

        assert game.id == 42
        assert game.title == "Half-Life"
        assert game.summary == "Ride the tram."
        assert game.file_size == 2048
        assert game.release == "1998-11-19"
        assert game.user_rating == 96
        assert game.critic_rating is None
        assert game.genres == ["Shooter"]
        assert game.cover == GameImage(id=5, type="COVER", blurhash="abc")
        assert game.header is None
        assert game.images == [GameImage(id=6, type="SCREENSHOT")]

    def test_get_games_tolerates_sparse_dto(self, client, session):
        route(session, sync={"games": [{"id": 1}]}, getByIds=[{"id": 1}])

        game = client.get_games()[0]

        assert game == Game(id=1, title="Unknown", library_id=0)

    def test_get_games_fetches_details_in_batches_and_keeps_order(
            self, client, session, monkeypatch):
        monkeypatch.setattr("gameyfin_frontend.services.gameyfin_api.GAME_DETAILS_BATCH_SIZE", 2)
        batches = []

        def get_by_ids(body):
            batches.append(body["ids"])
            # The server does not promise any order
            return [{"id": i, "title": f"Full {i}"} for i in reversed(body["ids"])]

        route(session, sync={"games": [{"id": i, "title": f"Summary {i}"} for i in (3, 1, 2)]},
              getByIds=get_by_ids)

        games = client.get_games()

        assert batches == [[3, 1], [2]]
        assert [g.title for g in games] == ["Full 3", "Full 1", "Full 2"]

    def test_get_games_keeps_summary_of_game_without_details(self, client, session):
        route(session, sync={"games": [{"id": 1, "title": "Gone", "fileSize": 10}]},
              getByIds=[])

        assert client.get_games() == [Game(id=1, title="Gone", library_id=0, file_size=10)]

    def test_falls_back_to_get_all_on_servers_before_2_5_1(self, client, session):
        route(session, getAll=[{"id": 1, "name": "PC", "gameIds": [10]}])

        assert client.get_libraries() == [Library(id=1, name="PC", game_ids=[10])]
        assert called_methods(session) == ["sync", "sync", "getAll"]  # sync retried for CSRF

        session.post.reset_mock()
        client.get_libraries()
        assert called_methods(session) == ["getAll"]

    def test_auth_error_when_both_apis_are_rejected(self, client, session):
        route(session)

        with pytest.raises(GameyfinAuthError):
            client.get_libraries()

    def test_no_legacy_retry_once_the_new_api_worked(self, client, session):
        route(session, sync={"libraries": []})
        client.get_libraries()
        route(session)
        session.post.reset_mock()

        with pytest.raises(GameyfinAuthError):
            client.get_libraries()
        assert "getAll" not in called_methods(session)

    def test_get_download_providers_sorted_by_priority(self, client, session):
        session.post.return_value = FakeResponse(payload=[
            {"key": "low", "name": "Low", "priority": 1},
            {"key": "high", "name": "High", "priority": 9},
        ])

        providers = client.get_download_providers()

        assert [p.key for p in providers] == ["high", "low"]
        assert providers[0] == DownloadProvider(key="high", name="High", priority=9)


class TestFetchImage:
    def test_returns_response_content(self, client, session):
        session.get.return_value = FakeResponse(content=b"PNGDATA")

        assert client.fetch_image(GameImage(id=1, type="COVER")) == b"PNGDATA"

    def test_http_error_raises_api_error(self, client, session):
        session.get.return_value = FakeResponse(status_code=404)

        with pytest.raises(GameyfinApiError):
            client.fetch_image(GameImage(id=1, type="COVER"))


class TestPing:
    def test_answering_server_passes(self, client, session):
        session.get.return_value = FakeResponse(status_code=302)

        client.ping()

        assert session.get.call_args.args[0] == "http://gameyfin.test:8080/"

    def test_unreachable_server_raises(self, client, session):
        session.get.side_effect = requests.ConnectTimeout("timed out")

        with pytest.raises(GameyfinConnectionError):
            client.ping(timeout=1)

    def test_proxy_without_upstream_raises(self, client, session):
        session.get.return_value = FakeResponse(status_code=502)

        with pytest.raises(GameyfinConnectionError):
            client.ping()
