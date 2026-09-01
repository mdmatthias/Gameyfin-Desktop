"""Translates gamepad input into Qt interactions across the whole application.

The navigator is intentionally generic: it works on whatever window is active
(main window, modal dialog, message box, file dialog) by collecting the
focusable widgets in it and moving focus geometrically.  That means new dialogs
are gamepad-navigable without extra wiring, as long as their controls accept
tab focus.

Special cases handled on top of the generic behaviour:

* the embedded web view, which is driven through :mod:`gamepad_webnav`
* item views whose rows contain their own buttons (Downloads, Prefixes) —
  navigation goes straight to the row buttons and the list selection follows
* text fields, sliders, spin boxes, combo boxes and tab bars, which want
  left/right for themselves
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QKeyEvent, QMouseEvent, QPainter, QPainterPath,
                         QRegion, QWheelEvent)
from PyQt6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QAbstractSlider, QApplication,
    QCheckBox, QComboBox, QDialog, QLineEdit, QListWidget, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QScrollBar, QSpinBox, QDoubleSpinBox,
    QTabBar, QTextEdit, QToolButton, QWidget, QStackedWidget,
)

from .gamepad import (
    BTN_A, BTN_B, BTN_LB, BTN_LT, BTN_RB, BTN_RT, BTN_START,
    BTN_Y, GamepadState,
)
from .gamepad_webnav import WebNavigator
from .widgets.gamepad_hud import BINDINGS, GamepadHelpOverlay, GamepadHintBar

logger = logging.getLogger(__name__)

# Sideways drift is penalised so navigation stays in the current row/column.
_ACROSS_PENALTY = 2.5

DEFAULT_SCROLL_SPEED = 60

_TEXT_WIDGETS = (QLineEdit, QPlainTextEdit, QTextEdit)
_CLICKABLE = (QPushButton, QToolButton, QCheckBox, QRadioButton)
# Widgets that use left/right themselves instead of moving focus.
_HORIZONTAL_CONSUMERS = (QAbstractSlider, QSpinBox, QDoubleSpinBox, QTabBar)

_web_view_class: Any = None
_web_view_lookup_done = False


def web_view_class() -> Any:
    """Return QWebEngineView lazily (importing it needs a live QApplication)."""
    global _web_view_class, _web_view_lookup_done
    if not _web_view_lookup_done:
        _web_view_lookup_done = True
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: PLC0415

            _web_view_class = QWebEngineView
        except ImportError:  # pragma: no cover - WebEngine is a hard dep in practice
            _web_view_class = None
    return _web_view_class


class FocusRing(QWidget):
    """A theme-independent highlight drawn around the focused widget.

    qt-material styles focus very subtly, which is unusable from a couch, so the
    ring is painted by us instead of fighting the stylesheet.

    The ring is a sibling widget laid *over* the focused one, so its interior is
    masked away: qt-material gives every plain ``QWidget`` an opaque
    ``background-color``, and without the mask the ring would simply hide the
    widget it is meant to point at. Masking is geometric and therefore immune to
    whatever the active theme paints.
    """

    MARGIN = 4
    THICKNESS = 3
    RADIUS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)
        self.hide()

    @classmethod
    def _rounded_region(cls, rect: QRect) -> QRegion:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), cls.RADIUS, cls.RADIUS)
        return QRegion(path.toFillPolygon().toPolygon())

    def _apply_mask(self) -> None:
        """Cut the interior out so only the ring band belongs to the widget."""
        outer = self.rect()
        inner = outer.adjusted(self.THICKNESS, self.THICKNESS, -self.THICKNESS, -self.THICKNESS)
        if inner.isEmpty():
            self.clearMask()
            return
        self.setMask(self._rounded_region(outer).subtracted(self._rounded_region(inner)))

    def paintEvent(self, event: Any) -> None:  # noqa: ANN401
        # The mask clips this to the band, giving a clean rounded outline.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 188, 212))
        painter.end()

    def follow(self, widget: QWidget | None) -> None:
        """Move the ring onto *widget*, or hide it when there is nothing to show."""
        host = self.parentWidget()
        if widget is None or host is None or not widget.isVisible():
            self.hide()
            return

        top_left = widget.mapTo(host, QPoint(0, 0))
        rect = QRect(top_left, widget.size()).adjusted(
            -self.MARGIN, -self.MARGIN, self.MARGIN, self.MARGIN
        )
        if not rect.intersects(host.rect()):
            self.hide()
            return

        # No raise_() here: the ring is created after the window's other
        # children so it already paints on top, and restacking siblings from a
        # focus/paint context is what destabilises Qt's repaint manager.
        self.setGeometry(rect)
        self._apply_mask()
        self.show()


class GamepadNavigator(QObject):
    """Connects a :class:`~gameyfin_frontend.gamepad.GamepadManager` to the UI."""

    def __init__(
        self,
        window: QWidget,
        manager: Any,
        settings: Any = None,
        hint_bar: GamepadHintBar | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self.window = window
        self.manager = manager
        self.settings = settings
        self.hint_bar = hint_bar

        self.enabled = True
        # Ignore input while another application (usually a running game) is focused.
        self.ignore_when_inactive = True

        self._scroll_speed = DEFAULT_SCROLL_SPEED
        self._scroll_remainder_x = 0.0
        self._scroll_remainder_y = 0.0

        # One ring per top-level window: it is a child widget, so it must live
        # in the window it highlights and die together with it.
        self.focus_ring = FocusRing(window)
        self._rings: dict[QWidget, FocusRing] = {window: self.focus_ring}
        self.help_overlay = GamepadHelpOverlay(window)

        manager.navigate.connect(self._on_navigate)
        manager.button_pressed.connect(self._on_button_pressed)
        manager.polled.connect(self._on_polled)
        manager.connected.connect(self._on_device_connected)
        manager.disconnected.connect(self._on_device_disconnected)

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
            # Intercept arrow keys on closed combo boxes so they open the
            # popup instead of Qt's default (cycle item + fire activated).
            app.installEventFilter(self)

        # The ring has to keep up with scrolling and relayouts, which emit no
        # focus signal of their own.
        self._ring_timer = QTimer(self)
        self._ring_timer.setInterval(80)
        self._ring_timer.timeout.connect(self._apply_ring)

        # Showing/raising the ring must never happen inside Qt's own focus or
        # show handling — doing so corrupts the widget repaint manager and
        # segfaults. Every request is therefore coalesced onto the event loop.
        self._ring_sync = QTimer(self)
        self._ring_sync.setSingleShot(True)
        self._ring_sync.setInterval(0)
        self._ring_sync.timeout.connect(self._apply_ring)

        tab_widget = getattr(window, "tab_widget", None)
        if tab_widget is not None:
            tab_widget.currentChanged.connect(self._on_tab_changed)

        self.reload_settings()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def reload_settings(self) -> None:
        """Re-read scroll speeds from the settings manager."""
        if not self.settings:
            return
        self._scroll_speed = self._int_setting("GF_GAMEPAD_SCROLL_SPEED", DEFAULT_SCROLL_SPEED, 5, 400)

    def _int_setting(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable gamepad-driven interaction (input is simply ignored)."""
        self.enabled = enabled
        if not enabled:
            self._hide_rings()
            self._ring_timer.stop()
            self.help_overlay.hide()
            if self.hint_bar is not None:
                self.hint_bar.hide()

    # ------------------------------------------------------------------
    # Event filter
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Intercept arrow keys on closed combo boxes.

        Without a gamepad: open the popup instead of Qt's default
        (cycle item + fire activated).

        With a gamepad connected: consume the event silently. The Steam
        Controller sends keyboard events alongside gamepad events; without
        this the keyboard event would cycle the combo box item while the
        gamepad event moves focus — double-navigation.
        """
        if event.type() == QEvent.Type.KeyPress:
            key_event = event  # type: QKeyEvent
            if isinstance(obj, QComboBox) and not obj.view().isVisible():
                if key_event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    if self.manager is not None and self.manager.is_connected():
                        return True  # swallow — gamepad handles navigation
                    obj.showPopup()
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Device state
    # ------------------------------------------------------------------

    def _on_device_connected(self, name: str) -> None:
        if self.hint_bar is not None:
            self.hint_bar.show()
        self._ring_timer.start()
        self._refresh_ring()
        self._update_hints(device=name)
        # _on_tab_changed only fires on an actual tab switch, so without this
        # the very first tab the app opens on (the web view) never gets an
        # initial focus target — the user has to switch away and back before
        # gamepad input does anything there.
        QTimer.singleShot(0, self.focus_first_in_current_tab)

    def _on_device_disconnected(self) -> None:
        self._ring_timer.stop()
        self._hide_rings()
        self.help_overlay.hide()
        if self.hint_bar is not None:
            self.hint_bar.hide()

    def _active(self) -> bool:
        """True when gamepad input should be acted upon right now."""
        if not self.enabled:
            logger.debug("_active=False: not enabled")
            return False
        if not self.ignore_when_inactive:
            return True
        app = QApplication.instance()
        if app is None:
            return False
        # A freshly opened modal dialog is registered in Qt's own modal stack
        # synchronously (inside exec()/show()), but the window manager's
        # activation handshake for the new top-level window can lag behind
        # that — so applicationState()/activeWindow() may briefly still look
        # inactive right after the dialog appears. Treat a modal widget as
        # active regardless, matching _active_window()'s precedence.
        if app.activeModalWidget() is not None:
            return True
        if app.applicationState() == Qt.ApplicationState.ApplicationActive:
            return True
        active = app.activeWindow()
        return active is not None

    # ------------------------------------------------------------------
    # Window / focus helpers
    # ------------------------------------------------------------------

    def _active_window(self) -> QWidget:
        app = QApplication.instance()
        if app is not None:
            modal = app.activeModalWidget()
            if modal is not None:
                return modal
            active = app.activeWindow()
            if active is not None:
                return active
        return self.window

    @staticmethod
    def _popup() -> QWidget | None:
        app = QApplication.instance()
        return app.activePopupWidget() if app is not None else None

    def _focus_widget(self) -> QWidget | None:
        """The focused widget of the window we are currently navigating."""
        window = self._active_window()
        app = QApplication.instance()
        widget = app.focusWidget() if app is not None else None
        if widget is not None and (window is None or widget.window() is window):
            return widget
        # The application-wide focus widget is None (or stale, pointing at
        # another window) while this window is not active, so fall back to the
        # focus the window itself remembers.
        return window.focusWidget() if window is not None else None

    @staticmethod
    def _send_key(target: QWidget | None, key: Qt.Key, text: str = "") -> None:
        """Deliver a synthetic key press/release to *target*."""
        if target is None:
            return
        receiver = target.focusWidget() or target
        for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            event = QKeyEvent(event_type, key, Qt.KeyboardModifier.NoModifier, text)
            QApplication.sendEvent(receiver, event)

    @staticmethod
    def _find_combo_box_popup(widget: QWidget | None) -> QComboBox | None:
        """Return the QComboBox whose popup is currently open and owns *widget*.

        *widget* is usually the combo box itself — ``QApplication.focusWidget()``
        reports the combo box, not its internal view, while the popup is open
        — but during directional navigation it can also be something inside
        the popup (the list view, or a row widget within it). Anchoring the
        match to the application's *current* active popup, rather than to
        ``widget``'s own top-level window, handles both shapes and also
        avoids matching a combo box whose popup has already closed but whose
        view still exists (e.g. a dialog just opened from the selection).
        """
        if widget is None:
            return None
        active_popup = QApplication.activePopupWidget()
        if active_popup is None:
            return None
        node: QWidget | None = widget
        while node is not None:
            if isinstance(node, QComboBox):
                view = node.view()
                if view is not None and view.window() is active_popup:
                    return node
            node = node.parentWidget()
        return None

    def web_view_for(self, widget: QWidget | None) -> Any:
        """Return the QWebEngineView *widget* belongs to, if any."""
        view_class = web_view_class()
        if view_class is None:
            return None
        node = widget
        while node is not None:
            if isinstance(node, view_class):
                return node
            node = node.parentWidget()
        return None

    def _current_web_view(self) -> Any:
        """The web view that should receive navigation, if one is in play."""
        if self._active_window() is not self.window:
            return None
        tab_widget = getattr(self.window, "tab_widget", None)
        if tab_widget is None:
            return None
        current = tab_widget.currentWidget()
        view_class = web_view_class()
        if view_class is not None and isinstance(current, view_class):
            return current
        # Tab 0 may wrap the browser + native library in a QStackedWidget;
        # check its current child.
        if isinstance(current, QStackedWidget):
            child = current.currentWidget()
            if view_class is not None and isinstance(child, view_class):
                return child
        return None

    @staticmethod
    def _is_inside_web_view(widget: QWidget | None, web_view: Any) -> bool:
        """True when *widget* is the web view or lives inside it."""
        if widget is None:
            return False
        node: QWidget | None = widget
        while node is not None:
            if node is web_view:
                return True
            node = node.parentWidget()
        return False

    # ------------------------------------------------------------------
    # Focus candidates
    # ------------------------------------------------------------------

    def _wraps_focusable_widgets(self, container: QWidget) -> bool:
        """True when a scrollable widget holds other focusable widgets."""
        return any(self._is_focusable(child) for child in container.findChildren(QWidget))

    def _is_focus_stop(self, container: QAbstractScrollArea) -> bool:
        """Whether a scrollable container is worth focusing in its own right.

        Scroll areas and item views accept focus themselves, but landing on one
        is a dead end when its content is navigable: the ring wraps the whole
        page and no direction can leave it, because every real target sits
        *inside* the container's rectangle. So a container is only a focus stop
        when it has rows of its own to select and nothing focusable inside.
        """
        # Text editors scroll, but they are leaf controls the user edits —
        # never containers to navigate into.
        if isinstance(container, _TEXT_WIDGETS):
            return True
        if self._wraps_focusable_widgets(container):
            return False
        if isinstance(container, QAbstractItemView):
            # An item view with NoSelection has no rows to select — it's just
            # a container for child widgets (e.g. the Prefixes tab QListWidget
            # whose rows contain QComboBox script pickers).  Don't let it
            # swallow focus.
            if container.selectionMode() == QAbstractItemView.SelectionMode.NoSelection:
                return False
            model = container.model()
            return model is not None and model.rowCount() > 0
        return False

    def _is_focusable(self, widget: QWidget) -> bool:
        policy = widget.focusPolicy()
        if not (policy & Qt.FocusPolicy.TabFocus):
            return False
        if not widget.isVisible() or not widget.isEnabled():
            return False
        # Only skip genuinely collapsed widgets. A control squeezed thin by an
        # awkward layout is still something the user must be able to reach.
        if widget.width() <= 0 or widget.height() <= 0:
            return False
        # Scroll bars are driven by the right stick, never focused.
        if isinstance(widget, QScrollBar):
            return False
        return True

    def candidates(self, root: QWidget) -> list[QWidget]:
        """Collect the widgets in *root* the gamepad may focus."""
        view_class = web_view_class()
        # Both the Downloads and Prefixes tabs nest a list of button-bearing
        # rows inside a scroll area; neither container may swallow the focus.
        skip_containers = {
            container for container in root.findChildren(QAbstractScrollArea)
            if not self._is_focus_stop(container)
        }

        # Collect the content widgets of all scroll areas so we can exclude
        # them — they are generic QWidget containers whose large bounding
        # box would otherwise win every spatial-score and trap focus.
        skip_content_widgets: set[QWidget] = set()
        for area in root.findChildren(QScrollArea):
            cw = area.widget()
            if cw is not None:
                skip_content_widgets.add(cw)

        found: list[QWidget] = []
        for widget in root.findChildren(QWidget):
            if widget in skip_containers:
                continue
            if widget in skip_content_widgets:
                continue
            if not self._is_focusable(widget):
                continue
            # A web view is one stop; its internal render widgets are not.
            if view_class is not None and not isinstance(widget, view_class):
                if self.web_view_for(widget.parentWidget()) is not None:
                    continue
            found.append(widget)
        return found

    @staticmethod
    def _rect_in(root: QWidget, widget: QWidget) -> QRect:
        return QRect(widget.mapTo(root, QPoint(0, 0)), widget.size())

    @staticmethod
    def _score(source: QRect, target: QRect, direction: str) -> float | None:
        """Cost of moving from *source* to *target*; None when out of direction.

        The off-axis term is the *gap* between the two rectangles along the
        across-axis: 0 when they overlap (same column/row) and the distance
        between them otherwise. A centre-distance term would additionally
        penalise a target that sits in the same column but has a different
        width — e.g. a slider that is narrower than the checkbox above it
        because a value label shares its row — and that penalty can outweigh
        the small vertical gap, making the closer widget unreachable.
        """
        if direction in ("up", "down"):
            if direction == "down":
                along = target.top() - source.bottom()
            else:
                along = source.top() - target.bottom()
            if along < -min(source.height(), target.height()) / 2:
                return None
            across = max(0, max(source.left() - target.right(), target.left() - source.right()))
        else:
            if direction == "right":
                along = target.left() - source.right()
            else:
                along = source.left() - target.right()
            if along < -min(source.width(), target.width()) / 2:
                return None
            across = max(0, max(source.top() - target.bottom(), target.top() - source.bottom()))
        return max(0, along) + across * _ACROSS_PENALTY

    def find_neighbour(self, root: QWidget, current: QWidget | None, direction: str) -> QWidget | None:
        """Return the best focus target from *current* in *direction*."""
        options = self.candidates(root)
        if not options:
            return None
        if current is None or current not in options:
            return self._first_candidate(root, options)

        source = self._rect_in(root, current)
        best: QWidget | None = None
        best_score = float("inf")
        for option in options:
            if option is current:
                continue
            score = self._score(source, self._rect_in(root, option), direction)
            if score is not None and score < best_score:
                best_score = score
                best = option

        if best is None:
            # Focus can still end up on a container (a click, or Qt's own
            # default focus). Everything inside it fails the direction test, so
            # step into its content instead of leaving the user stuck.
            best = self._first_contained(root, current, options)
        return best

    def _first_contained(
        self, root: QWidget, container: QWidget, options: list[QWidget]
    ) -> QWidget | None:
        """The topmost focus candidate living inside *container*, if any."""
        inside = [option for option in options
                  if option is not container and container.isAncestorOf(option)]
        if not inside:
            return None
        return min(inside, key=lambda w: (self._rect_in(root, w).top(),
                                          self._rect_in(root, w).left()))

    def _first_candidate(self, root: QWidget, options: list[QWidget] | None = None) -> QWidget | None:
        options = options if options is not None else self.candidates(root)
        if not options:
            return None
        return min(options, key=lambda w: (self._rect_in(root, w).top(), self._rect_in(root, w).left()))

    # ------------------------------------------------------------------
    # Focus application
    # ------------------------------------------------------------------

    def focus_widget(self, widget: QWidget | None) -> None:
        """Focus *widget*, scroll it into view and sync any list selection."""
        if widget is None:
            return
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        self.ensure_visible(widget)
        self.sync_list_selection(widget)
        self._refresh_ring()

    def list_item_for(self, widget: QWidget) -> tuple[QListWidget | None, Any]:
        """Find the list and item whose row widget contains *widget*."""
        node: QWidget | None = widget
        while node is not None:
            view: QWidget | None = node.parentWidget()
            while view is not None and not isinstance(view, QListWidget):
                view = view.parentWidget()
            if isinstance(view, QListWidget):
                for row in range(view.count()):
                    item = view.item(row)
                    if view.itemWidget(item) is node:
                        return view, item
            node = node.parentWidget()
        return None, None

    def ensure_visible(self, widget: QWidget) -> None:
        """Scroll every enclosing scroll area so *widget* is on screen."""
        view, item = self.list_item_for(widget)
        if view is not None and item is not None:
            view.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

        node: QWidget | None = widget.parentWidget()
        while node is not None:
            if isinstance(node, QScrollArea):
                node.ensureWidgetVisible(widget, 60, 60)
            node = node.parentWidget()

    def sync_list_selection(self, widget: QWidget) -> None:
        """Keep a list's selection on the row that owns the focused widget.

        The Prefixes tab enables its Configure/Delete buttons from the list
        selection, so focus and selection must not drift apart.
        """
        view, item = self.list_item_for(widget)
        if view is None or item is None:
            return
        if view.selectionMode() == QAbstractItemView.SelectionMode.NoSelection:
            return
        if view.currentItem() is not item:
            view.setCurrentItem(item)

    def _ring_for(self, host: QWidget) -> FocusRing:
        """Return (creating if needed) the focus ring living in *host*."""
        ring = self._rings.get(host)
        if ring is None:
            ring = FocusRing(host)
            self._rings[host] = ring
            host.destroyed.connect(lambda _=None, key=host: self._rings.pop(key, None))
        return ring

    def _hide_rings(self, keep: FocusRing | None = None) -> None:
        for ring in list(self._rings.values()):
            if ring is not keep:
                ring.hide()

    def _refresh_ring(self) -> None:
        """Request a ring update on the next event loop pass."""
        self._ring_sync.start()

    def _apply_ring(self) -> None:
        if not self.enabled or not self.manager.is_connected():
            self._hide_rings()
            return

        widget = self._focus_widget()
        if widget is None or self.web_view_for(widget) is not None:
            # Inside the page the highlight is drawn by the injected script.
            self._hide_rings()
            return

        host = widget.window()
        if host is None:
            self._hide_rings()
            return

        ring = self._ring_for(host)
        self._hide_rings(keep=ring)
        ring.follow(widget)

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        if new is not None:
            self.sync_list_selection(new)
        self._refresh_ring()

    def _on_tab_changed(self, _index: int) -> None:
        if not self.manager.is_connected():
            return
        QTimer.singleShot(0, self.focus_first_in_current_tab)

    def focus_first_in_current_tab(self) -> None:
        """Put focus on something sensible after switching tabs."""
        tab_widget = getattr(self.window, "tab_widget", None)
        if tab_widget is None:
            return
        page = tab_widget.currentWidget()
        if page is None:
            return

        web_view = self._current_web_view()
        if web_view is not None:
            web_view.setFocus(Qt.FocusReason.OtherFocusReason)
            WebNavigator(web_view).focus_first()
            return

        self.focus_widget(self._first_candidate(page))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_navigate(self, direction: str) -> None:
        if not self._active():
            logger.debug(
                "_on_navigate(%s): blocked by _active()=False "
                "(activeModalWidget=%r activeWindow=%r applicationState=%r)",
                direction,
                QApplication.instance().activeModalWidget() if QApplication.instance() else None,
                QApplication.instance().activeWindow() if QApplication.instance() else None,
                QApplication.instance().applicationState() if QApplication.instance() else None,
            )
            return

        if self.help_overlay.isVisible():
            logger.debug("_on_navigate(%s): blocked by help_overlay being visible", direction)
            return

        # Explicitly handle combo box popups — their internal list view is the
        # one that handles navigation, not the popup container itself, and
        # definitely not the combo box: QApplication.focusWidget() reports the
        # combo box while the popup is open, and combo.focusWidget() doesn't
        # delegate down to the view either, so a raw arrow key sent to the
        # combo box hits QComboBox's own key handling — which, for a raw key
        # event, treats it as its closed-popup behaviour (cycle to the next
        # item and fire activated() immediately) rather than moving the
        # popup's highlight.
        focused = self._focus_widget()
        combo = self._find_combo_box_popup(focused)
        if combo is not None:
            view = combo.view()
            logger.debug(
                "_on_navigate(%s): routed to combo box popup %r view=%r (focused=%r)",
                direction, combo, view, focused,
            )
            key = {
                "up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down,
                "left": Qt.Key.Key_Left, "right": Qt.Key.Key_Right,
            }[direction]
            self._send_key(view, key)
            return

        popup = self._popup()
        if popup is not None:
            logger.debug(
                "_on_navigate(%s): routed to activePopupWidget %r (focused=%r, visible=%r)",
                direction, popup, focused, popup.isVisible(),
            )
            key = {
                "up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down,
                "left": Qt.Key.Key_Left, "right": Qt.Key.Key_Right,
            }[direction]
            self._send_key(popup, key)
            return

        web_view = self._current_web_view()
        focused = self._focus_widget()
        if web_view is not None and self._is_inside_web_view(focused, web_view):
            WebNavigator(web_view).move(direction)
            return

        # Web view is current but nothing inside it is focused — route
        # navigation into the page so the user can browse the embedded UI.
        if web_view is not None and focused is None:
            WebNavigator(web_view).move(direction)
            return

        root = self._active_window()

        if focused is not None and self._handle_widget_navigation(focused, direction):
            return

        target = self.find_neighbour(root, focused, direction)
        logger.debug(
            "_on_navigate(%s): root=%r focused=%r -> target=%r",
            direction, root, focused, target,
        )
        if target is not None:
            self.focus_widget(target)
            return

        # Nothing to focus that way — scroll instead so long lists still move.
        self._scroll_step(direction)

    def _handle_widget_navigation(self, widget: QWidget, direction: str) -> bool:
        """Let certain widgets consume the direction themselves.

        Returns True when the input was handled and focus must not move.
        """
        horizontal = direction in ("left", "right")

        if isinstance(widget, QTabBar) and horizontal:
            step = 1 if direction == "right" else -1
            new_index = widget.currentIndex() + step
            if 0 <= new_index < widget.count():
                widget.setCurrentIndex(new_index)
            return True

        if isinstance(widget, _HORIZONTAL_CONSUMERS) and horizontal:
            key = Qt.Key.Key_Right if direction == "right" else Qt.Key.Key_Left
            self._send_key(widget, key)
            return True

        if isinstance(widget, QAbstractItemView):
            key = {
                "down": Qt.Key.Key_Down,
                "up": Qt.Key.Key_Up,
                "right": Qt.Key.Key_Right,
                "left": Qt.Key.Key_Left,
            }[direction]
            before = widget.currentIndex()
            self._send_key(widget, key)
            after = widget.currentIndex()
            # Only claim the input if the selection actually moved in the
            # requested direction.  For IconMode, left/right may still
            # change the row (Qt wraps to the next / previous item) but the
            # visual position hasn't shifted in that direction — focus
            # should be allowed to leave the grid at its edge.
            if after != before:
                if isinstance(widget, QListWidget) and widget.viewMode() == QListWidget.ViewMode.IconMode:
                    old_rect = widget.visualRect(before)
                    new_rect = widget.visualRect(after)
                    if direction == "right" and new_rect.center().x() <= old_rect.center().x():
                        return False
                    if direction == "left" and new_rect.center().x() >= old_rect.center().x():
                        return False
                return True
            return False

        return False

    def _scroll_step(self, direction: str) -> None:
        if direction == "up":
            self._scroll_pixels(0, -self._scroll_speed)
        elif direction == "down":
            self._scroll_pixels(0, self._scroll_speed)
        elif direction == "left":
            self._scroll_pixels(-self._scroll_speed, 0)
        else:
            self._scroll_pixels(self._scroll_speed, 0)

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _on_button_pressed(self, button: str) -> None:
        if not self._active():
            return

        if self.help_overlay.isVisible():
            if button in (BTN_B, BTN_START, BTN_A):
                self.help_overlay.hide()
            return

        handler = {
            BTN_A: self._activate,
            BTN_B: self._back,
            BTN_Y: self._refresh,
            BTN_LB: lambda: self._switch_tab(-1),
            BTN_RB: lambda: self._switch_tab(1),
            BTN_LT: lambda: self._page_scroll(-1),
            BTN_RT: lambda: self._page_scroll(1),
            BTN_START: self._toggle_help,
        }.get(button)

        if handler is not None:
            handler()

    # -- A -----------------------------------------------------------------

    def _activate(self) -> None:
        widget = self._focus_widget()

        # A combo box popup must be confirmed through the combo box's own
        # API (see _activate_combo_popup) rather than the generic popup
        # fallback below — sending it a synthetic Return key never runs
        # whatever QComboBoxPrivateContainer does on a real click to close
        # itself, so it must be checked first.
        combo = self._find_combo_box_popup(widget)
        if combo is not None:
            self._activate_combo_popup(combo)
            return

        popup = self._popup()
        if popup is not None:
            self._send_key(popup, Qt.Key.Key_Return)
            return

        web_view = self.web_view_for(widget)
        if web_view is not None:
            self._activate_web_element(web_view)
            return

        if widget is None:
            self.focus_widget(self._first_candidate(self._active_window()))
            return

        if isinstance(widget, _CLICKABLE):
            widget.animateClick()
            return

        if isinstance(widget, QComboBox):
            widget.showPopup()
            return

        if isinstance(widget, _TEXT_WIDGETS + (QSpinBox, QDoubleSpinBox)):
            # Already has real Qt keyboard focus from D-pad navigation, which
            # is what the platform's own on-screen keyboard (e.g. Steam Deck's
            # gamescope overlay) keys off of — nothing left to do here.
            return

        if isinstance(widget, QAbstractItemView):
            self._activate_list(widget)
            return

        self._send_key(widget, Qt.Key.Key_Space, " ")

    def _activate_combo_popup(self, combo: QComboBox) -> None:
        """Confirm the highlighted row of an open combo box popup.

        Manually emitting the view's ``activated`` signal (as the generic list
        path does) never runs whatever QComboBox's popup container normally
        does to close itself on a real click — it only reacts to actual
        events on the view, not a signal fired by our own code. That leaves
        Qt's global active-popup-widget stuck pointing at the now-orphaned
        container, which then silently swallows every future gamepad press
        (in this dialog and any dialog opened afterwards) because
        :meth:`_on_navigate` routes input to whatever ``_popup()`` reports.
        Driving the combo box's own API instead guarantees the popup is torn
        down the same way a real click would.

        The ``activated`` emission itself is deferred to the next event loop
        turn — exactly what :meth:`QAbstractButton.animateClick` does for
        buttons — rather than fired synchronously here. A handler connected
        to ``activated`` may open a modal dialog (``exec()``), and this call
        is still nested deep inside :class:`~gameyfin_frontend.gamepad.GamepadManager`'s
        poll timer callback; calling ``exec()`` from there means it never
        returns until the dialog closes, holding that entire call chain (poll
        timer → button handler → this method) suspended for the dialog's
        whole lifetime. Buttons dodge this because ``animateClick()`` already
        defers the real click by ~100ms. Deferring the emission the same way
        lets the poll call return first, so the dialog opens from a clean,
        top-level turn of the event loop instead of a re-entrant one.
        """
        view = combo.view()
        index = view.currentIndex() if view is not None else None
        if index is None or not index.isValid():
            combo.hidePopup()
            return
        row = index.row()
        combo.setCurrentIndex(row)
        combo.hidePopup()
        QTimer.singleShot(0, lambda: combo.activated.emit(row))

    def _activate_list(self, view: QAbstractItemView) -> None:
        """Activate the current row, falling back to the dialog's accept button."""
        index = view.currentIndex()
        if index.isValid():
            view.activated.emit(index)
            if isinstance(view, QListWidget):
                item = view.item(index.row())
                if item is not None:
                    view.itemActivated.emit(item)
            return
        self._send_key(view, Qt.Key.Key_Return)

    def _activate_web_element(self, web_view: Any) -> None:
        """Click the focused page element with a real mouse event.

        A scripted ``el.click()`` runs inside the page's script engine and
        never carries Chromium's transient user-activation flag, so anything
        gated on a genuine gesture — starting a file download, ``window.open``,
        the platform's on-screen keyboard on a text input — silently no-ops.
        Fetch the element's on-screen point and dispatch a real QMouseEvent at
        it instead, the same trick :meth:`_send_wheel_scroll` already uses for
        wheel input.
        """
        WebNavigator(web_view).activate(lambda point: self._click_web_point(web_view, point))

    def _click_web_point(self, web_view: Any, point: Any) -> None:
        if not point:
            return
        target = web_view.focusProxy() or web_view
        pos = QPointF(point["x"], point["y"])
        global_pos = QPointF(target.mapToGlobal(pos.toPoint()))
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, pos, global_pos,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease, pos, global_pos,
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(target, press)
        QApplication.sendEvent(target, release)

    # -- B -----------------------------------------------------------------

    def _back(self) -> None:
        popup = self._popup()
        if popup is not None:
            popup.close()
            return

        window = self._active_window()
        if window is not self.window:
            # Escape is what dialogs, message boxes and file dialogs understand.
            self._send_key(window, Qt.Key.Key_Escape)
            if isinstance(window, QDialog) and window.isVisible():
                window.reject()
            return

        tab_widget = getattr(self.window, "tab_widget", None)
        if tab_widget is None:
            return

        web_view = self._current_web_view()
        if web_view is not None and web_view.history().canGoBack():
            web_view.back()
            return

        from .config import FIXED_TAB_COUNT  # noqa: PLC0415 - avoids an import cycle at module load

        index = tab_widget.currentIndex()
        if index >= FIXED_TAB_COUNT:
            close_tab = getattr(self.window, "close_tab", None)
            if close_tab is not None:
                close_tab(index)
        elif index != 0:
            tab_widget.setCurrentIndex(0)

    # -- Y -----------------------------------------------------------------

    def _refresh(self) -> None:
        if self._active_window() is not self.window:
            return

        web_view = self._current_web_view()
        if web_view is not None:
            web_view.reload()
            return

        tab_widget = getattr(self.window, "tab_widget", None)
        current = tab_widget.currentWidget() if tab_widget is not None else None
        refresh = getattr(current, "refresh_prefixes", None)
        if callable(refresh):
            refresh()

    # -- shoulders / triggers ---------------------------------------------

    def _switch_tab(self, step: int) -> None:
        if self._active_window() is not self.window or self._popup() is not None:
            return
        tab_widget = getattr(self.window, "tab_widget", None)
        if tab_widget is None or tab_widget.count() == 0:
            return
        index = (tab_widget.currentIndex() + step) % tab_widget.count()
        tab_widget.setCurrentIndex(index)

    def _page_scroll(self, direction: int) -> None:
        web_view = self._current_web_view()
        if web_view is not None:
            self._send_wheel_scroll(web_view, 0, web_view.height() * 0.85 * direction)
            return

        area = self._scroll_area()
        if area is None:
            return
        bar = area.verticalScrollBar()
        bar.triggerAction(
            QAbstractSlider.SliderAction.SliderPageStepAdd if direction > 0
            else QAbstractSlider.SliderAction.SliderPageStepSub
        )

    # ------------------------------------------------------------------
    # Scrolling
    # ------------------------------------------------------------------

    def _scroll_area(self) -> QAbstractScrollArea | None:
        """The scroll area the right stick should act on."""
        node: QWidget | None = self._focus_widget()
        while node is not None:
            if isinstance(node, QAbstractScrollArea):
                return node
            node = node.parentWidget()

        window = self._active_window()
        for area in window.findChildren(QAbstractScrollArea):
            if area.isVisible():
                return area
        return None

    def _scroll_pixels(self, dx: float, dy: float) -> None:
        web_view = self._current_web_view()
        if web_view is not None:
            self._send_wheel_scroll(web_view, dx, dy)
            return

        area = self._scroll_area()
        if area is None:
            return
        if dy:
            bar = area.verticalScrollBar()
            bar.setValue(bar.value() + int(dy))
        if dx:
            bar = area.horizontalScrollBar()
            bar.setValue(bar.value() + int(dx))

    @staticmethod
    def _send_wheel_scroll(web_view: Any, dx: float, dy: float) -> None:
        """Scroll the embedded page with a real wheel event.

        ``window.scrollBy()`` injected through ``page.runJavaScript()`` takes
        an entirely different path through the page's script engine than
        actual input does and — unlike a real mouse wheel, confirmed working
        — doesn't reliably move Chromium's compositor viewport here.
        Synthesising the same ``QWheelEvent`` a physical wheel produces uses
        the code path already known to work. Empirically (tested against a
        bare QWebEngineView), Chromium's handling here is driven by
        ``angleDelta`` alone — a ``pixelDelta``-only event with a null
        ``angleDelta`` is ignored outright — and the relationship is linear:
        2 angle units per pixel, opposite sign (a positive ``dy``, "scroll
        down", is a negative angle, matching a real wheel rotated toward the
        user).
        """
        target = web_view.focusProxy() or web_view
        pos = QPointF(target.rect().center())
        global_pos = QPointF(target.mapToGlobal(target.rect().center()))
        angle_delta = QPoint(int(dx * -2), int(dy * -2))
        if angle_delta.isNull():
            return
        event = QWheelEvent(
            pos, global_pos, angle_delta, angle_delta,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(target, event)

    def _on_polled(self, state: GamepadState) -> None:
        if not self._active():
            return

        deadzone = self.manager.deadzone
        x = state.right_x if abs(state.right_x) > deadzone else 0.0
        y = state.right_y if abs(state.right_y) > deadzone else 0.0
        if not x and not y:
            self._scroll_remainder_x = self._scroll_remainder_y = 0.0
            return

        # A poll tick is ~16 ms; scale so the setting reads as "pixels per frame".
        self._scroll_remainder_x += x * self._scroll_speed * 0.25
        self._scroll_remainder_y += y * self._scroll_speed * 0.25
        step_x = int(self._scroll_remainder_x)
        step_y = int(self._scroll_remainder_y)
        if not step_x and not step_y:
            return
        self._scroll_remainder_x -= step_x
        self._scroll_remainder_y -= step_y

        self._scroll_pixels(step_x, step_y)

    # ------------------------------------------------------------------
    # Help / hints
    # ------------------------------------------------------------------

    def _toggle_help(self) -> None:
        if self._active_window() is not self.window:
            return
        self.help_overlay.setGeometry(self.window.rect())
        self.help_overlay.toggle()

    def _update_hints(self, device: str | None = None) -> None:
        if self.hint_bar is None:
            return

        name = device if device is not None else self.manager.device_name
        self.hint_bar.set_status(name or "No controller")

        self.hint_bar.set_hints(BINDINGS)
