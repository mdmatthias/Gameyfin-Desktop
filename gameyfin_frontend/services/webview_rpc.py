"""Run Hilla RPC calls inside the logged-in web view.

Mirroring the web view's cookie jar into a ``requests`` session is fragile: the
session cookie may be scoped in ways a flat name→value dict cannot express, and
Hilla's CSRF token has three possible sources (Spring cookie, ``_csrf`` meta tag,
Vaadin session token). Issuing the request from inside the page sidesteps both
problems — the browser attaches exactly the credentials the working web app uses,
and the CSRF token is read from that same document.

``call()`` must run on the GUI thread, which owns the page. It does not freeze it:
the HTTP work happens in the renderer process, so the call waits in a nested event
loop and the interface keeps painting meanwhile.
"""

import json
import logging
from typing import Any, Callable

from PyQt6.QtCore import (QCoreApplication, QEventLoop, QObject, QThread,
                          QTimer)

from ..config import API_TIMEOUT, HILLA_PREFIX

logger = logging.getLogger(__name__)

# Resolves the CSRF header the same way Hilla's CsrfInfoSource does, then issues a
# synchronous XHR so runJavaScript can return the result (it cannot await promises).
# Only the renderer blocks, and the page is hidden while the native UI is in front.
_CALL_JS = """
(function() {
    function meta(name) {
        var el = document.head.querySelector('meta[name="' + name + '"]');
        return el && el.content && el.content.toLowerCase() !== 'undefined' ? el.content : null;
    }
    function cookie(name) {
        var parts = document.cookie ? document.cookie.split('; ') : [];
        for (var i = 0; i < parts.length; i++) {
            if (parts[i].indexOf(name + '=') === 0) {
                return decodeURIComponent(parts[i].substring(name.length + 1));
            }
        }
        return null;
    }
    function csrf() {
        var spring = cookie('XSRF-TOKEN') || meta('_csrf');
        if (spring) {
            return [meta('_csrf_header') || 'X-XSRF-TOKEN', spring];
        }
        var vaadin = cookie('csrfToken');
        if (!vaadin && window.Vaadin && window.Vaadin.TypeScript) {
            vaadin = window.Vaadin.TypeScript.csrfToken;
        }
        return ['X-CSRF-Token', vaadin || ''];
    }

    try {
        var header = csrf();
        var xhr = new XMLHttpRequest();
        xhr.open('POST', %(url)s, false);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.setRequestHeader('Accept', 'application/json');
        if (header[1]) {
            xhr.setRequestHeader(header[0], header[1]);
        }
        xhr.withCredentials = true;
        xhr.send(%(body)s);
        return JSON.stringify({
            status: xhr.status,
            body: xhr.responseText,
            csrfHeader: header[0],
            csrfPresent: !!header[1]
        });
    } catch (e) {
        return JSON.stringify({status: 0, error: String(e)});
    }
})();
"""


class WebViewRpcError(Exception):
    """Raised when a call could not be carried out in the page."""


class WebViewRpc(QObject):
    """Executes Hilla endpoint calls in the web view's page.

    Args:
        page_provider: Callable returning the ``QWebEnginePage`` to run in, or
            None when no page is available yet.
        timeout: Seconds to wait for a call to come back.
    """

    def __init__(self, page_provider: Callable[[], Any],
                 timeout: float = API_TIMEOUT, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.page_provider = page_provider
        self.timeout = timeout

    @staticmethod
    def on_gui_thread() -> bool:
        """Return True when the caller runs on the thread that owns the page."""
        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() is app.thread()

    def available(self) -> bool:
        """Return True when a page is available and this thread may talk to it."""
        return self.page_provider() is not None and self.on_gui_thread()

    def call(self, endpoint: str, method: str,
             params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run ``POST <prefix>/<endpoint>/<method>`` inside the page.

        Returns:
            A dict with ``status`` (int, 0 when the request never left the page)
            and ``body`` (str), plus CSRF diagnostics.

        Raises:
            WebViewRpcError: Not on the GUI thread, no page available, or the
                script did not return a usable result.
        """
        if not self.on_gui_thread():
            raise WebViewRpcError("Web view calls must be made on the GUI thread")

        page = self.page_provider()
        if page is None:
            raise WebViewRpcError("No web view page available for API calls")

        script = _CALL_JS % {
            "url": json.dumps(f"{HILLA_PREFIX}/{endpoint}/{method}"),
            "body": json.dumps(json.dumps(params or {})),
        }

        state: dict[str, Any] = {"done": False, "value": None}
        loop = QEventLoop()

        def done(value: Any) -> None:
            state["done"] = True
            state["value"] = value
            loop.quit()

        try:
            page.runJavaScript(script, 0, done)
        except (RuntimeError, TypeError) as e:
            raise WebViewRpcError(f"Could not run {endpoint}.{method} in the page: {e}") from e

        if not state["done"]:
            # Only the renderer is busy, so the GUI keeps running while we wait
            QTimer.singleShot(int(self.timeout * 1000), loop.quit)
            loop.exec()

        if not state["done"]:
            raise WebViewRpcError(f"Timed out running {endpoint}.{method} in the web view")

        raw = state["value"]
        if not isinstance(raw, str) or not raw:
            raise WebViewRpcError(f"{endpoint}.{method} returned no result from the web view")

        try:
            payload = json.loads(raw)
        except ValueError as e:
            raise WebViewRpcError(f"{endpoint}.{method} returned malformed data: {e}") from e

        if payload.get("error"):
            raise WebViewRpcError(f"{endpoint}.{method} failed in the page: {payload['error']}")

        logger.debug(
            "%s.%s via web view -> HTTP %s (csrf header %s, token present: %s)",
            endpoint, method, payload.get("status"),
            payload.get("csrfHeader"), payload.get("csrfPresent"),
        )
        return payload
