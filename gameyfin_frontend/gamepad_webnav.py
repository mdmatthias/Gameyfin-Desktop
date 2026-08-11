"""Gamepad navigation inside the embedded Gameyfin web UI.

Qt has no concept of focus inside a web page, so directional navigation is done
by a small JavaScript helper injected into every page of the profile.  It picks
the nearest focusable element in the requested direction, highlights it and
scrolls it into view.  :class:`WebNavigator` is the thin Python side that calls
into it.
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
        '[contenteditable="true"]',
        /* The screenshot gallery on a game's detail page is a Swiper.js
           carousel whose slides are plain, non-interactive-by-markup <div>s
           with a click handler bound in JS (opening a zoom lightbox) —
           no href/role/tabindex, so without this they're entirely
           unreachable and never a candidate to move focus onto or
           highlight, regardless of any highlight-rendering fix. */
        '.swiper-slide'
    ].join(',');

    var RING_ID = 'gameyfin-gamepad-ring';

    /* The ring used to be a CSS class toggled on the focused element itself
       (with a `::after` overlay to dodge ancestor `overflow: hidden` clipping
       — see the git history for that whole saga). That broke against the
       screenshot gallery's Swiper.js carousel: Swiper periodically rewrites
       a slide's whole `className` from scratch to manage its own state
       classes, silently wiping ours within tens of milliseconds of it being
       set — confirmed by sampling the class shortly after applying it.
       Tracking position from a single independent overlay, appended
       directly to <body> and never touching the target element at all,
       sidesteps that (and, as a bonus, ancestor clipping entirely — a
       position:fixed element appended to <body> escapes any ancestor's
       `overflow`, whereas the old ::after approach only worked because a
       card's clip box happened to hug its bounds exactly). */
    function ensureRing() {
        var ring = document.getElementById(RING_ID);
        if (ring) { return ring; }
        ring = document.createElement('div');
        ring.id = RING_ID;
        ring.style.cssText =
            'position: fixed;' +
            'pointer-events: none;' +
            'z-index: 2147483647;' +
            'box-sizing: border-box;' +
            'border: 3px solid #00bcd4;' +
            'border-radius: 4px;' +
            'display: none;';
        (document.body || document.documentElement).appendChild(ring);
        return ring;
    }

    var ringTarget = null;

    /* Re-measures ringTarget and repositions the ring to match. Called right
       after selecting something, and on an interval, since the target can
       move on its own afterwards — a smooth scrollIntoView still animating,
       a carousel transitioning, a window resize — with no signal of its own
       for us to react to. */
    function syncRing() {
        var ring = document.getElementById(RING_ID);
        if (!ring) { return; }
        if (!ringTarget || !document.contains(ringTarget) || !isVisible(ringTarget)) {
            ring.style.display = 'none';
            return;
        }
        var r = ringTarget.getBoundingClientRect();
        ring.style.display = 'block';
        ring.style.top = r.top + 'px';
        ring.style.left = r.left + 'px';
        ring.style.width = r.width + 'px';
        ring.style.height = r.height + 'px';
    }

    setInterval(syncRing, 100);

    function isVisible(el) {
        if (el.disabled) { return false; }
        var rect = el.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) { return false; }
        var style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') { return false; }
        if (parseFloat(style.opacity || '1') < 0.05) { return false; }
        return true;
    }

    /* Elements matched only via a custom selector like `.swiper-slide` are
       plain <div>s with no tabindex — calling .focus() on those is simply
       ignored by the browser, so document.activeElement would never
       actually become the element move() just selected. tabindex="-1"
       makes .focus() work while staying out of the page's own Tab order. */
    var NATIVE_FOCUSABLE = /^(A|BUTTON|SELECT|TEXTAREA|INPUT)$/;

    function ensureFocusable(el) {
        if (NATIVE_FOCUSABLE.test(el.tagName)) { return; }
        if (!el.hasAttribute('tabindex')) { el.setAttribute('tabindex', '-1'); }
    }

    function candidates() {
        var found = [];
        var nodes = document.querySelectorAll(SELECTOR);
        for (var i = 0; i < nodes.length; i++) {
            if (isVisible(nodes[i])) {
                ensureFocusable(nodes[i]);
                found.push(nodes[i]);
            }
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
       sideways, which keeps navigation inside the current column/row.

       Both branches additionally *require* some overlap on the cross axis
       with the source element (gap === 0). Without that, once a shelf or
       grid runs out of rendered cards (see the virtualisation note below)
       the "closest by score" fallback happily jumps to whatever unrelated
       element — a header button, a card in a different shelf, the page
       footer far below a large virtualised library grid — happens to sit
       nearest on screen, and the user is stuck there since nothing in that
       new context continues in the same direction either. Requiring
       cross-axis overlap forces that case to fall through to
       scrollAndRetry instead of escaping the shelf/grid. */
    function score(fromRect, toRect, direction) {
        var a = centre(fromRect);
        var b = centre(toRect);
        var along, across, gap;

        if (direction === 'up' || direction === 'down') {
            along = direction === 'down' ? toRect.top - fromRect.bottom : fromRect.top - toRect.bottom;
            if (along < -Math.min(fromRect.height, toRect.height) / 2) { return null; }
            gap = Math.max(0, Math.max(fromRect.left - toRect.right, toRect.left - fromRect.right));
            if (gap > 0) { return null; }
            across = Math.abs(a.x - b.x) + gap;
        } else {
            along = direction === 'right' ? toRect.left - fromRect.right : fromRect.left - toRect.right;
            if (along < -Math.min(fromRect.width, toRect.width) / 2) { return null; }
            gap = Math.max(0, Math.max(fromRect.top - toRect.bottom, toRect.top - fromRect.bottom));
            if (gap > 0) { return null; }
            across = Math.abs(a.y - b.y) + gap;
        }
        return Math.max(0, along) + across * 2.5;
    }

    /* Gameyfin's homepage rows (e.g. the "GOG" shelf) are virtualised with
       react-window: only the cards near the current horizontal scroll
       position exist in the DOM. Moving past the last *rendered* card in a
       row therefore finds no candidate even though more cards are one
       scroll away. Walk up from the current element to find the scrollable
       row container, nudge its scroll position, and retry once react-window
       has had a chance to mount the newly-visible cells.

       Large vertical grids (e.g. a library with hundreds of games) don't
       necessarily scroll through an inner `overflow: auto` wrapper at all —
       the grid's own wrapper is sized to its full content height (its
       scrollHeight equals its clientHeight) and the *page* scrolls instead,
       relying on the window/document to reveal more rows as react-window
       watches scroll position. The ancestor walk below stops at
       document.body without ever considering the document, so fall back to
       the page's own scrolling element when nothing inner qualifies. */
    function scrollableAncestor(el, direction) {
        var horizontal = (direction === 'left' || direction === 'right');
        var node = el ? el.parentElement : null;
        while (node && node !== document.body) {
            var style = window.getComputedStyle(node);
            if (horizontal) {
                if (node.scrollWidth > node.clientWidth + 4 &&
                    (style.overflowX === 'auto' || style.overflowX === 'scroll')) {
                    return node;
                }
            } else if (node.scrollHeight > node.clientHeight + 4 &&
                       (style.overflowY === 'auto' || style.overflowY === 'scroll')) {
                return node;
            }
            node = node.parentElement;
        }
        if (!horizontal) {
            var root = document.scrollingElement || document.documentElement;
            if (root.scrollHeight > root.clientHeight + 4) { return root; }
        }
        return null;
    }

    /* True when *el* sits inside a position:fixed/sticky ancestor (a header,
       nav bar, etc). Those elements keep the same on-screen coordinates no
       matter how far the page scrolls, which makes them a magnet for the
       lost-focus recovery path below: after several scroll-and-retry rounds
       its reference point is a large extrapolation rather than a real
       element's position, and something that never moves increasingly looks
       like "the closest thing" as that extrapolation drifts. Ordinary
       navigation (a real focused element to move from) never hits this and
       can still reach header controls same as before. */
    function isFixedChrome(el) {
        var node = el;
        while (node && node !== document.body) {
            var position = window.getComputedStyle(node).position;
            if (position === 'fixed' || position === 'sticky') { return true; }
            node = node.parentElement;
        }
        return false;
    }

    function bestCandidate(fromRect, direction, exclude, avoidChrome) {
        var list = candidates();
        var best = null;
        var bestScore = Infinity;
        for (var i = 0; i < list.length; i++) {
            if (list[i] === exclude) { continue; }
            if (avoidChrome && isFixedChrome(list[i])) { continue; }
            var value = score(fromRect, list[i].getBoundingClientRect(), direction);
            if (value !== null && value < bestScore) {
                bestScore = value;
                best = list[i];
            }
        }
        return best;
    }

    /* Guards against overlapping retry chains: a D-pad held at the gamepad's
       default repeat rate (140ms) fires faster than one full scroll+re-render
       round trip can settle, so without this a run of presses stacks up
       several scrollBy() calls on top of each other and overshoots well past
       the card that was actually being reached for. */
    var scrollLocks = new WeakSet();

    function scrollAndRetry(from, direction, attemptsLeft) {
        if (attemptsLeft <= 0) { return; }
        var container = scrollableAncestor(from, direction);
        if (!container || scrollLocks.has(container)) { return; }
        scrollLocks.add(container);

        var horizontal = (direction === 'left' || direction === 'right');
        var before = horizontal ? container.scrollLeft : container.scrollTop;
        var delta = (horizontal ? container.clientWidth : container.clientHeight) * 0.6;
        if (direction === 'left' || direction === 'up') { delta = -delta; }
        container.scrollBy(horizontal ? {left: delta} : {top: delta});

        setTimeout(function () {
            scrollLocks.delete(container);
            var after = horizontal ? container.scrollLeft : container.scrollTop;
            if (Math.abs(after - before) < 1) { return; } // already at the scroll limit

            var best = bestCandidate(from.getBoundingClientRect(), direction, from);
            if (best) { select(best); return; }
            scrollAndRetry(from, direction, attemptsLeft - 1);
        }, 90);
    }

    function highlight(el) {
        ringTarget = el;
        if (el) { ensureRing(); }
        syncRing();
    }

    /* Remembers where the focused element was on screen, in case react-window
       recycles its DOM node once it scrolls far enough out of the render
       window — several held presses down a long library grid is enough to
       get there. When that happens document.activeElement silently resets
       and move() has no element left to measure from or walk up from for a
       scrollable ancestor; lastRect lets it keep heading in the same
       direction from the same place instead of falling back to "first
       visible thing", which on this site is liable to be a sticky header
       control rather than anything in the grid the user was browsing. */
    var lastRect = null;

    function select(el) {
        highlight(el);
        try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
        if (el.scrollIntoView) {
            el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
        }
        lastRect = el.getBoundingClientRect();
    }

    function shiftRect(rect, dx, dy) {
        return {
            top: rect.top - dy, bottom: rect.bottom - dy,
            left: rect.left - dx, right: rect.right - dx,
            width: rect.width, height: rect.height
        };
    }

    /* Used only once the previously focused element has been recycled away:
       there's no element left to find a scrollable ancestor from, but on
       this site that case only arises for the page-level vertical scroll
       (see the scrollableAncestor note above), so scroll the document
       directly and retry against lastRect, translated by however far the
       scroll actually moved. */
    function scrollDocumentAndRetry(fromRect, direction, attemptsLeft) {
        if (attemptsLeft <= 0) { return; }
        var root = document.scrollingElement || document.documentElement;
        if (scrollLocks.has(root)) { return; }
        scrollLocks.add(root);

        var horizontal = (direction === 'left' || direction === 'right');
        var before = horizontal ? root.scrollLeft : root.scrollTop;
        var delta = (horizontal ? root.clientWidth : root.clientHeight) * 0.6;
        if (direction === 'left' || direction === 'up') { delta = -delta; }
        root.scrollBy(horizontal ? {left: delta} : {top: delta});

        setTimeout(function () {
            scrollLocks.delete(root);
            var after = horizontal ? root.scrollLeft : root.scrollTop;
            var scrolled = after - before;
            if (Math.abs(scrolled) < 1) { return; }

            var adjusted = horizontal ? shiftRect(fromRect, scrolled, 0) : shiftRect(fromRect, 0, scrolled);
            var best = bestCandidate(adjusted, direction, null, true);
            if (best) { select(best); return; }
            scrollDocumentAndRetry(adjusted, direction, attemptsLeft - 1);
        }, 90);
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
            if (!from) {
                // The focused element may simply be gone — recycled by
                // virtualisation after scrolling several screens past it —
                // rather than navigation never having started. Keep heading
                // the same direction from where it was instead of jumping to
                // "first visible thing", which can land on page chrome.
                if (lastRect) {
                    var lastBest = bestCandidate(lastRect, direction, null, true);
                    if (lastBest) { select(lastBest); return true; }
                    scrollDocumentAndRetry(lastRect, direction, 5);
                    return false;
                }
                return first();
            }

            var best = bestCandidate(from.getBoundingClientRect(), direction, from);
            if (best) { select(best); return true; }

            // Nothing rendered yet in that direction — likely a virtualised
            // row whose next card hasn't been mounted. Scroll its container
            // and retry asynchronously.
            scrollAndRetry(from, direction, 5);
            return false;
        },

        focusFirst: function () { return first(); },

        clear: function () { highlight(null); },

        activate: function () {
            /* Returns the on-screen point of the focused element instead of
               calling .click() itself — a scripted click runs inside the
               page's script engine and never carries Chromium's transient
               user-activation flag, so anything gated on a genuine gesture
               (starting a file download, window.open, the platform's own
               on-screen keyboard on a text input) silently no-ops. The Python
               side dispatches a real mouse event at this point instead. */
            var el = current();
            if (!el) { return null; }
            var target = el;
            if (el.classList.contains('swiper-slide')) {
                // The gallery's zoom-lightbox handler is bound to the slide's
                // <img> specifically, not the slide wrapper — a click on a
                // parent dispatches an event *at* the parent, which never
                // reaches a listener bound only to the child.
                target = el.querySelector('img') || el;
            }
            var rect = target.getBoundingClientRect();
            return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
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

    def activate(self, callback: Callable[[Any], None]) -> None:
        """Fetch the on-screen point of the focused element, for a real click."""
        self._run(f"window.{NAV_OBJECT}.activate()", callback)
