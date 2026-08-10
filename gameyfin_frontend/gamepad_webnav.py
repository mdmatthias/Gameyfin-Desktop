"""Gamepad navigation inside the embedded Gameyfin web UI.

Qt has no concept of focus inside a web page, so directional navigation is done
by a small JavaScript helper injected into every page of the profile.  It picks
the nearest focusable element in the requested direction, highlights it and
scrolls it into view.  :class:`WebNavigator` is the thin Python side that calls
into it and reports back what happened (so the caller can, for example, open the
on-screen keyboard when a text field was activated).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Namespaced on purpose — the script runs in the page's main world.
NAV_OBJECT = "__gameyfinGamepadNav"

NAV_SCRIPT = """
(function () {
    if (window.%(obj)s) { return; }

    var SELECTOR = [
        'a[href]', 'button', 'select', 'textarea',
        'input:not([type="hidden"])',
        '[tabindex]:not([tabindex="-1"])',
        '[role="button"]', '[role="link"]', '[role="menuitem"]',
        '[role="option"]', '[role="tab"]', '[role="checkbox"]',
        '[contenteditable="true"]'
    ].join(',');

    var STYLE_ID = 'gameyfin-gamepad-focus-style';
    var CLASS_NAME = 'gameyfin-gamepad-focus';

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) { return; }
        var style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = '.' + CLASS_NAME + ' {' +
            'outline: 3px solid #00bcd4 !important;' +
            'outline-offset: 2px !important;' +
            'border-radius: 4px;' +
            'scroll-margin: 96px;' +
            '}';
        (document.head || document.documentElement).appendChild(style);
    }

    function isVisible(el) {
        if (el.disabled) { return false; }
        var rect = el.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) { return false; }
        var style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') { return false; }
        if (parseFloat(style.opacity || '1') < 0.05) { return false; }
        return true;
    }

    function candidates() {
        var found = [];
        var nodes = document.querySelectorAll(SELECTOR);
        for (var i = 0; i < nodes.length; i++) {
            if (isVisible(nodes[i])) { found.push(nodes[i]); }
        }
        return found;
    }

    function current() {
        var el = document.activeElement;
        if (el && el !== document.body && isVisible(el)) { return el; }
        return null;
    }

    function centre(rect) {
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }

    /* Distance along the travel axis plus a heavy penalty for drifting
       sideways, which keeps navigation inside the current column/row. */
    function score(fromRect, toRect, direction) {
        var a = centre(fromRect);
        var b = centre(toRect);
        var along, across, gap;

        if (direction === 'up' || direction === 'down') {
            along = direction === 'down' ? toRect.top - fromRect.bottom : fromRect.top - toRect.bottom;
            if (along < -Math.min(fromRect.height, toRect.height) / 2) { return null; }
            gap = Math.max(0, Math.max(fromRect.left - toRect.right, toRect.left - fromRect.right));
            across = Math.abs(a.x - b.x) + gap;
        } else {
            along = direction === 'right' ? toRect.left - fromRect.right : fromRect.left - toRect.right;
            if (along < -Math.min(fromRect.width, toRect.width) / 2) { return null; }
            gap = Math.max(0, Math.max(fromRect.top - toRect.bottom, toRect.top - fromRect.bottom));
            across = Math.abs(a.y - b.y) + gap;
        }
        return Math.max(0, along) + across * 2.5;
    }

    function highlight(el) {
        var previous = document.querySelectorAll('.' + CLASS_NAME);
        for (var i = 0; i < previous.length; i++) {
            previous[i].classList.remove(CLASS_NAME);
        }
        if (el) {
            ensureStyle();
            el.classList.add(CLASS_NAME);
        }
    }

    function select(el) {
        highlight(el);
        try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
        if (el.scrollIntoView) {
            el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
        }
    }

    function first() {
        var list = candidates();
        var best = null;
        var bestScore = Infinity;
        for (var i = 0; i < list.length; i++) {
            var rect = list[i].getBoundingClientRect();
            if (rect.bottom < 0 || rect.top > window.innerHeight) { continue; }
            var value = Math.max(0, rect.top) * 2 + Math.max(0, rect.left);
            if (value < bestScore) { bestScore = value; best = list[i]; }
        }
        if (!best && list.length) { best = list[0]; }
        if (best) { select(best); return true; }
        return false;
    }

    window.%(obj)s = {
        move: function (direction) {
            var from = current();
            if (!from) { return first(); }

            var fromRect = from.getBoundingClientRect();
            var list = candidates();
            var best = null;
            var bestScore = Infinity;

            for (var i = 0; i < list.length; i++) {
                if (list[i] === from) { continue; }
                var value = score(fromRect, list[i].getBoundingClientRect(), direction);
                if (value !== null && value < bestScore) {
                    bestScore = value;
                    best = list[i];
                }
            }
            if (best) { select(best); return true; }
            return false;
        },

        focusFirst: function () { return first(); },

        clear: function () { highlight(null); },

        /* Returns a descriptor so the app knows whether to open the
           on-screen keyboard instead of just clicking. */
        activate: function () {
            var el = current();
            if (!el) { return JSON.stringify({ kind: 'none' }); }

            var tag = (el.tagName || '').toLowerCase();
            var type = (el.getAttribute('type') || '').toLowerCase();
            var textual = tag === 'textarea' ||
                el.isContentEditable ||
                (tag === 'input' && ['text', 'search', 'email', 'url', 'tel', 'number', 'password', ''].indexOf(type) !== -1);

            if (textual) {
                return JSON.stringify({
                    kind: 'text',
                    value: el.isContentEditable ? el.textContent : (el.value || ''),
                    multiline: tag === 'textarea',
                    password: type === 'password',
                    label: el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || ''
                });
            }

            el.click();
            return JSON.stringify({ kind: 'click' });
        },

        setText: function (value) {
            var el = current();
            if (!el) { return false; }
            if (el.isContentEditable) {
                el.textContent = value;
            } else {
                var setter = Object.getOwnPropertyDescriptor(
                    el.constructor.prototype, 'value');
                if (setter && setter.set) { setter.set.call(el, value); }
                else { el.value = value; }
            }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        },

        submit: function () {
            var el = current();
            if (!el) { return false; }
            ['keydown', 'keypress', 'keyup'].forEach(function (name) {
                el.dispatchEvent(new KeyboardEvent(name, {
                    key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
                }));
            });
            if (el.form && el.form.requestSubmit) {
                try { el.form.requestSubmit(); } catch (e) { /* ignore */ }
            }
            return true;
        },

        scrollBy: function (dx, dy) {
            window.scrollBy(dx, dy);
            /* Gameyfin renders its grid inside a scrolling container, so also
               nudge the nearest scrollable ancestor of the focused element. */
            var el = current();
            while (el && el !== document.body) {
                if (el.scrollHeight > el.clientHeight + 4) { el.scrollTop += dy; break; }
                el = el.parentElement;
            }
        },

        scrollPage: function (factor) {
            window.scrollBy(0, window.innerHeight * factor * 0.85);
        }
    };
})();
""" % {"obj": NAV_OBJECT}


def build_nav_script() -> Any:
    """Return a QWebEngineScript that installs the navigation helper."""
    from PyQt6.QtWebEngineCore import QWebEngineScript  # noqa: PLC0415 - lazy, needs QApplication

    script = QWebEngineScript()
    script.setName("gameyfin-gamepad-nav")
    script.setSourceCode(NAV_SCRIPT)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script


class WebNavigator:
    """Drives :data:`NAV_SCRIPT` inside a single :class:`QWebEngineView`."""

    def __init__(self, view: Any) -> None:
        self.view = view

    def _run(self, expression: str, callback: Callable[[Any], None] | None = None) -> None:
        page = self.view.page() if self.view else None
        if page is None:
            return
        script = f"(window.{NAV_OBJECT} ? {expression} : null)"
        try:
            if callback is None:
                page.runJavaScript(script)
            else:
                page.runJavaScript(script, 0, callback)
        except RuntimeError as exc:  # page torn down mid-call
            logger.debug("Web navigation call failed: %s", exc)

    def move(self, direction: str) -> None:
        self._run(f"window.{NAV_OBJECT}.move({json.dumps(direction)})")

    def focus_first(self) -> None:
        self._run(f"window.{NAV_OBJECT}.focusFirst()")

    def clear(self) -> None:
        self._run(f"window.{NAV_OBJECT}.clear()")

    def scroll_by(self, dx: float, dy: float) -> None:
        self._run(f"window.{NAV_OBJECT}.scrollBy({dx:.1f}, {dy:.1f})")

    def scroll_page(self, factor: float) -> None:
        self._run(f"window.{NAV_OBJECT}.scrollPage({factor:.2f})")

    def set_text(self, value: str) -> None:
        self._run(f"window.{NAV_OBJECT}.setText({json.dumps(value)})")

    def submit(self) -> None:
        self._run(f"window.{NAV_OBJECT}.submit()")

    def activate(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Activate the focused element; the callback receives its descriptor."""

        def _on_result(raw: Any) -> None:
            descriptor: dict[str, Any] = {"kind": "none"}
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        descriptor = parsed
                except json.JSONDecodeError:
                    logger.debug("Unexpected activate() payload: %r", raw)
            callback(descriptor)

        self._run(f"window.{NAV_OBJECT}.activate()", _on_result)
