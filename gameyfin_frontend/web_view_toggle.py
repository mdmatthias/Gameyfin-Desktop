"""A "Native view" button injected into the Gameyfin web app.

The button switches the desktop client to the native library UI. It sits left
of Gameyfin's "Requests" button in the header, copying its style; when there is
no such button it floats in the bottom-right corner. A click is reported back
as a console message (see ``CustomWebEnginePage``), which needs no QWebChannel
setup in the page. The button only shows on Gameyfin's own pages
(the Vaadin app), so an SSO login page never gets it, and not on the login or
setup pages, where the native library can't be used yet.
"""

from typing import Any

# Console message the button logs when clicked
NATIVE_UI_MESSAGE = "gameyfin-desktop:native-ui"

TOGGLE_SCRIPT = """
(function () {
    if (window.__gameyfinViewToggle) { return; }
    window.__gameyfinViewToggle = true;
    var ID = 'gameyfin-desktop-native-view';
    var FLOATING_STYLE = [
        'position:fixed', 'right:16px', 'bottom:16px', 'z-index:2147483647',
        'padding:8px 14px', 'border-radius:999px', 'cursor:pointer',
        'border:1px solid rgba(255,255,255,0.25)', 'background:rgba(30,34,40,0.9)',
        'color:#fff', 'font:600 13px sans-serif', 'box-shadow:0 2px 8px rgba(0,0,0,0.4)'
    ].join(';');

    function wanted() {
        var path = location.pathname;
        return !!window.Vaadin && path.indexOf('/login') !== 0 && path.indexOf('/setup') !== 0;
    }

    // Gameyfin's "Requests" button in the header, if the server shows it. Found
    // by its text, since the markup around it (icon, wrappers, tags) may vary.
    function requestsButton() {
        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        var node;
        while ((node = walker.nextNode())) {
            if (node.nodeValue.trim().toLowerCase() !== 'requests') { continue; }
            var el = node.parentElement;
            if (!el || el.closest('#' + ID)) { continue; }
            return el.closest('a, button, [role="button"]') || el;
        }
        return null;
    }

    // A grid icon shaped like the one in *anchor*, so both buttons share
    // the same layout (and line up) and look alike
    function iconLike(anchor) {
        var theirs = anchor.querySelector('svg');
        if (!theirs) { return null; }
        var NS = 'http://www.w3.org/2000/svg';
        var size = theirs.getBoundingClientRect();
        var svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('width', String(Math.round(size.width) || 16));
        svg.setAttribute('height', String(Math.round(size.height) || 16));
        svg.setAttribute('fill', 'currentColor');
        svg.setAttribute('aria-hidden', 'true');
        if (theirs.getAttribute('class')) { svg.setAttribute('class', theirs.getAttribute('class')); }
        [[3, 3], [13, 3], [3, 13], [13, 13]].forEach(function (pos) {
            var rect = document.createElementNS(NS, 'rect');
            rect.setAttribute('x', pos[0]);
            rect.setAttribute('y', pos[1]);
            rect.setAttribute('width', '8');
            rect.setAttribute('height', '8');
            rect.setAttribute('rx', '1.5');
            svg.appendChild(rect);
        });
        return svg;
    }

    function create() {
        var button = document.createElement('button');
        button.id = ID;
        button.type = 'button';
        button.textContent = 'Native view';
        button.title = 'Switch to the native library (Gameyfin Desktop)';
        button.addEventListener('click', function () { console.log(%(message)s); });
        return button;
    }

    function sync() {
        var button = document.getElementById(ID);
        if (!wanted()) {
            if (button) { button.remove(); }
            return;
        }
        if (!document.body) { return; }
        button = button || create();

        var anchor = requestsButton();
        if (anchor && anchor.parentNode) {
            // Left of "Requests", styled like it
            if (button.nextSibling !== anchor) {
                button.className = anchor.className;
                if (!button.querySelector('svg')) {
                    var icon = iconLike(anchor);
                    if (icon) { button.insertBefore(icon, button.firstChild); }
                }
                var gap = getComputedStyle(anchor.parentNode).columnGap;
                // Space it out unless the header's layout already does
                button.style.cssText = 'cursor:pointer;'
                    + (gap && gap !== 'normal' && gap !== '0px' ? '' : 'margin-right:8px;');
                anchor.parentNode.insertBefore(button, anchor);
            }
        } else if (button.parentNode !== document.body) {
            // No header button to sit next to: float in the corner
            button.className = '';
            button.style.cssText = FLOATING_STYLE;
            var stray = button.querySelector('svg');
            if (stray) { stray.remove(); }
            document.body.appendChild(button);
        }
    }

    // The app routes client-side and re-renders freely, so keep checking
    setInterval(sync, 1000);
    sync();
})();
""" % {"message": repr(NATIVE_UI_MESSAGE)}


def build_toggle_script() -> Any:
    """Return a QWebEngineScript that adds the "Native view" button."""
    from PyQt6.QtWebEngineCore import QWebEngineScript  # noqa: PLC0415 - lazy, needs QApplication

    script = QWebEngineScript()
    script.setName("gameyfin-view-toggle")
    script.setSourceCode(TOGGLE_SCRIPT)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script
