"""Tests for the in-page RPC transport and the client's use of it."""

import json
import threading
from unittest.mock import MagicMock

import pytest

from gameyfin_frontend.services.gameyfin_api import (GameyfinApiClient,
                                                     GameyfinApiError,
                                                     GameyfinAuthError)
from gameyfin_frontend.services.webview_rpc import WebViewRpc, WebViewRpcError


class FakePage:
    """Stands in for a QWebEnginePage, answering runJavaScript immediately."""

    def __init__(self, payload=None, raises=None, result=None):
        self.payload = payload
        self.raises = raises
        self.result = result
        self.scripts: list[str] = []

    def runJavaScript(self, script, world, callback):  # noqa: N802 - Qt naming
        self.scripts.append(script)
        if self.raises is not None:
            raise self.raises
        if self.result is not None:
            callback(self.result)
            return
        callback(json.dumps(self.payload))


@pytest.fixture()
def settings():
    stub = MagicMock()
    stub.get.side_effect = lambda key, fallback=None: (
        "http://gameyfin.test" if key == "GF_URL" else fallback
    )
    return stub


class TestWebViewRpc:
    def test_call_returns_status_and_body(self, qtbot):
        page = FakePage({"status": 200, "body": "[]", "csrfHeader": "X-CSRF-Token",
                         "csrfPresent": True})
        rpc = WebViewRpc(lambda: page)

        result = rpc.call("GameEndpoint", "getAll")

        assert result["status"] == 200
        assert result["body"] == "[]"

    def test_script_targets_the_endpoint_and_carries_the_params(self, qtbot):
        page = FakePage({"status": 200, "body": "null"})
        rpc = WebViewRpc(lambda: page)

        rpc.call("GameEndpoint", "deleteGame", {"gameId": 5})

        script = page.scripts[0]
        assert '"/connect/GameEndpoint/deleteGame"' in script
        assert json.dumps(json.dumps({"gameId": 5})) in script
        # Hilla's own CSRF resolution order must be reproduced in the page
        assert "XSRF-TOKEN" in script and "_csrf_header" in script
        assert "csrfToken" in script and "X-CSRF-Token" in script
        assert "withCredentials" in script

    def test_missing_page_raises(self, qtbot):
        rpc = WebViewRpc(lambda: None)

        assert not rpc.available()
        with pytest.raises(WebViewRpcError):
            rpc.call("GameEndpoint", "getAll")

    def test_script_failure_in_the_page_raises(self, qtbot):
        page = FakePage({"status": 0, "error": "NetworkError"})
        rpc = WebViewRpc(lambda: page)

        with pytest.raises(WebViewRpcError):
            rpc.call("GameEndpoint", "getAll")

    def test_non_string_result_raises(self, qtbot):
        page = FakePage(result=None)
        page.payload = None
        rpc = WebViewRpc(lambda: page)
        page.result = 42

        with pytest.raises(WebViewRpcError):
            rpc.call("GameEndpoint", "getAll")

    def test_malformed_result_raises(self, qtbot):
        page = FakePage()
        page.result = "not json"
        rpc = WebViewRpc(lambda: page)

        with pytest.raises(WebViewRpcError):
            rpc.call("GameEndpoint", "getAll")

    def test_runjavascript_error_is_reported(self, qtbot):
        page = FakePage(raises=RuntimeError("page gone"))
        rpc = WebViewRpc(lambda: page)

        with pytest.raises(WebViewRpcError):
            rpc.call("GameEndpoint", "getAll")

    def test_call_from_a_worker_thread_is_refused(self, qtbot):
        """The page is GUI-thread bound, so off-thread calls must fail fast."""
        page = FakePage({"status": 200, "body": "[]"})
        rpc = WebViewRpc(lambda: page)
        errors = []

        def worker():
            try:
                rpc.call("GameEndpoint", "getAll")
            except WebViewRpcError as e:
                errors.append(str(e))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert errors and "GUI thread" in errors[0]
        assert page.scripts == []

    def test_not_available_off_the_gui_thread(self, qtbot):
        page = FakePage({"status": 200, "body": "[]"})
        rpc = WebViewRpc(lambda: page)
        seen = []

        thread = threading.Thread(target=lambda: seen.append(rpc.available()))
        thread.start()
        thread.join(timeout=5)

        assert seen == [False]
        assert rpc.available() is True


class TestClientWithTransport:
    def test_transport_is_used_instead_of_http(self, qtbot, settings):
        session = MagicMock()
        page = FakePage({"status": 200, "body": json.dumps([{"id": 1, "name": "PC"}])})
        client = GameyfinApiClient(settings, session=session,
                                   rpc_transport=WebViewRpc(lambda: page))

        libraries = client.get_libraries()

        assert [lib.name for lib in libraries] == ["PC"]
        session.post.assert_not_called()

    def test_transport_401_is_an_auth_error(self, qtbot, settings):
        page = FakePage({"status": 401, "body": ""})
        client = GameyfinApiClient(settings, session=MagicMock(),
                                   rpc_transport=WebViewRpc(lambda: page))

        with pytest.raises(GameyfinAuthError):
            client.get_libraries()

    def test_transport_server_error_is_an_api_error(self, qtbot, settings):
        page = FakePage({"status": 500, "body": "boom"})
        client = GameyfinApiClient(settings, session=MagicMock(),
                                   rpc_transport=WebViewRpc(lambda: page))

        with pytest.raises(GameyfinApiError):
            client.get_libraries()

    def test_falls_back_to_http_without_a_page(self, qtbot, settings):
        session = MagicMock()
        session.get.return_value = MagicMock(text="<html></html>")
        response = MagicMock(status_code=200, content=b"[]", text="[]")
        session.post.return_value = response
        client = GameyfinApiClient(settings, session=session,
                                   rpc_transport=WebViewRpc(lambda: None))

        assert client.get_libraries() == []
        session.post.assert_called_once()

    def test_falls_back_to_http_when_the_page_errors(self, qtbot, settings):
        session = MagicMock()
        session.get.return_value = MagicMock(text="<html></html>")
        session.post.return_value = MagicMock(status_code=200, content=b"[]", text="[]")
        page = FakePage(raises=RuntimeError("page gone"))
        client = GameyfinApiClient(settings, session=session,
                                   rpc_transport=WebViewRpc(lambda: page))

        assert client.get_libraries() == []
        session.post.assert_called_once()
