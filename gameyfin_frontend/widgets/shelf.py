"""The bookshelf look of the native library: boxed games with their spines out.

Three pieces work together:

* :class:`BookcaseFrame` holds the list and paints the wooden case around it.
* :class:`LibraryGrid` is the library's list view. In shelf mode it paints the
  back wall and a plank under every row, and lays out the row of the active
  game (the one under the mouse, else the selected one) itself: that box slides
  half out of the shelf, turning so its cover shows, while the boxes to its
  right move aside (into free room at the end of the row, or by packing a
  little tighter). The boxes to its left stay put, so the box under the mouse
  never moves away from it.
* :class:`ShelfDelegate` paints a spine — a strip of the cover's artwork with
  the title — and the turned box, plus the one caption naming it.

Boxes vary in thickness like real ones do (fixed per game, see
:func:`spine_thickness`). The whole shelf scales with :func:`set_shelf_scale`.

Qt still decides which game sits in which row, so scrolling, paging and the
arrow keys behave exactly as in the classic cover grid, and both views share
the same items and data roles.
"""

import math
import random

from PyQt6.QtCore import (QEasingCurve, QEvent, QModelIndex, QPointF, QRectF, QSize, Qt,
                          QTimer, QVariantAnimation)
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPolygonF, QTransform)
from PyQt6.QtWidgets import (QListWidget, QStyle, QStyleOptionViewItem, QVBoxLayout,
                             QWidget)

from ..config import (SHELF_BOX_GAP, SHELF_BOX_HEIGHT, SHELF_BOX_WIDTH, SHELF_FRAME,
                      SHELF_HEADROOM, SHELF_HOVER_DELAY_MS, SHELF_PLANK_HEIGHT, SHELF_PULL,
                      SHELF_SCALE_DEFAULT, SHELF_SCALE_MAX, SHELF_SCALE_MIN, SHELF_SLIDE_MS,
                      SHELF_SPINE_MAX, SHELF_SPINE_MIN, SHELF_TILT)
from ..services.gameyfin_api import Game
from ..utils import accent_color, contrasting_text_color
from .cover_tile import CoverTileDelegate
from .game_box import _font_to_fit

# Wood of the bookcase: plank faces, the board's top, the frame and the back wall
_PLANK = QColor(92, 62, 38)
_PLANK_TOP = QColor(126, 88, 56)
_FRAME = QColor(78, 52, 32)
_WALL = QColor(38, 27, 19)

# Box front height / width when a game has no cover to take it from, and the
# range covers are clamped to so odd artwork still makes a box shape
DEFAULT_ASPECT = 1.4
_MIN_ASPECT, _MAX_ASPECT = 1.1, 1.6

_grain_cache: dict[tuple, QImage] = {}

# Every shelf size in config is multiplied by this (GF_LIBRARY_SHELF_SCALE)
_scale = SHELF_SCALE_DEFAULT / 100


def set_shelf_scale(percent: float) -> None:
    """Size the shelf at *percent* of its normal size (clamped to the allowed range)."""
    global _scale
    _scale = min(SHELF_SCALE_MAX, max(SHELF_SCALE_MIN, float(percent))) / 100


def shelf_scale() -> float:
    """The shelf's current size factor (1.0 = normal)."""
    return _scale


def _px(value: float) -> float:
    return value * _scale


def _shelf_offset() -> int:
    """From the top of a slot down to the plank the boxes stand on."""
    return round(_px(SHELF_HEADROOM + SHELF_BOX_HEIGHT))


def _plank_height() -> int:
    return round(_px(SHELF_PLANK_HEIGHT))


def spine_thickness(game_id: int) -> int:
    """How thick a game's box is: varied like real boxes, but fixed per game."""
    spread = SHELF_SPINE_MAX - SHELF_SPINE_MIN + 1
    # Knuth's multiplicative hash spreads neighbouring ids over the range
    return round(_px(SHELF_SPINE_MIN + (game_id * 2654435761 % 2 ** 32) % spread))


def shelf_item_size(thickness: int | None = None) -> QSize:
    """Size of one game's slot on the shelf: its spine, a hairline gap, headroom and plank."""
    if thickness is None:
        thickness = round(_px(SHELF_SPINE_MIN))
    return QSize(thickness + 1, _shelf_offset() + _plank_height())


def cover_aspect(width: int, height: int) -> float:
    """Height / width of the box a cover of this size is printed on."""
    if width <= 0 or height <= 0:
        return DEFAULT_ASPECT
    return min(_MAX_ASPECT, max(_MIN_ASPECT, height / width))


def box_front_size(aspect: float) -> tuple[float, float]:
    """(width, height) of a box front of *aspect* that fits on a shelf."""
    width, height = _px(SHELF_BOX_WIDTH), _px(SHELF_BOX_WIDTH) * aspect
    if height > _px(SHELF_BOX_HEIGHT):
        height = _px(SHELF_BOX_HEIGHT)
        width = height / aspect
    return width, height


def _wood(colour: QColor, width: int, height: int, seed: int, strength: float = 1.0) -> QImage:
    """A tile of horizontal wood grain in *colour* (cached; it never changes).

    *strength* scales how strongly the grain lines stand out.
    """
    key = (colour.rgb(), width, height, seed, strength)
    cached = _grain_cache.get(key)
    if cached is not None:
        return cached
    rng = random.Random(seed)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(colour)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for _ in range(max(8, height)):
        y = rng.uniform(0, height)
        wave, phase = rng.uniform(0.3, 1.6), rng.uniform(0, 6.3)
        darker = rng.random() < 0.65
        shade = QColor(0, 0, 0) if darker else QColor(255, 255, 255)
        shade.setAlpha(int(rng.randint(4, 16) * strength))
        painter.setPen(QPen(shade, rng.uniform(0.6, 1.6)))
        path = QPainterPath(QPointF(0, y))
        steps = 16
        for step in range(1, steps + 1):
            x = width * step / steps
            # The waves repeat every tile width, so the tiled grain has no seams
            offset = wave * math.sin(phase + 2 * math.tau * step / steps)
            path.lineTo(x, y + offset)
        painter.drawPath(path)
    painter.end()
    _grain_cache[key] = image
    return image


def _with_alpha(colour: QColor, alpha: int) -> QColor:
    out = QColor(colour)
    out.setAlpha(alpha)
    return out


def render_shelf_spine(game: Game, width: int, height: int, base: QColor,
                       cover: QImage | None) -> QImage:
    """A box's spine as it stands on the shelf.

    Printed boxes carry their artwork round onto the spine, so the spine is a
    strip of the cover (darkened so the title reads) between two bands in the
    cover's edge colour, with the title running top to bottom.
    """
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    rect = QRectF(0, 0, width, height)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if cover is not None and not cover.isNull():
        # The middle of the cover, where its subject usually is
        slice_width = min(cover.width(), cover.height() * width / height)
        source = QRectF((cover.width() - slice_width) / 2, 0, slice_width, cover.height())
        painter.drawImage(rect, cover, source)
        shade = QLinearGradient(0, 0, width, 0)
        shade.setColorAt(0, QColor(0, 0, 0, 150))
        shade.setColorAt(0.5, QColor(0, 0, 0, 95))
        shade.setColorAt(1, QColor(0, 0, 0, 150))
        painter.fillRect(rect, shade)
    else:
        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0, base.darker(135))
        gradient.setColorAt(0.5, base)
        gradient.setColorAt(1, base.darker(135))
        painter.fillRect(rect, gradient)

    # A publisher's livery: colour bands at either end
    band = max(4.0, height * 0.06)
    for y in (0.0, height - band):
        painter.fillRect(QRectF(0, y, width, band), base.darker(115))
    painter.fillRect(QRectF(0, band, width, max(1.0, height * 0.006)), QColor(255, 255, 255, 60))
    painter.fillRect(QRectF(0, height - band - max(1.0, height * 0.006), width,
                            max(1.0, height * 0.006)), QColor(255, 255, 255, 60))

    painter.translate(width, 0)
    painter.rotate(90)
    # Now x runs down the spine and y across it
    title = game.title.upper()
    room = height - 2 * band - height * 0.06
    font = _font_to_fit(title, width * 0.46, room, bold=True)
    painter.setFont(font)
    title_rect = QRectF(band + height * 0.03, 0, room, width)
    painter.setPen(QColor(0, 0, 0, 170))
    painter.drawText(title_rect.translated(width * 0.03, width * 0.04),
                     int(Qt.AlignmentFlag.AlignCenter), title)
    painter.setPen(QColor(255, 255, 255, 245))
    painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), title)
    painter.end()
    return image


class BookcaseFrame(QWidget):
    """Holds the library list; in shelf mode it paints the wooden case around it."""

    def __init__(self, grid: "LibraryGrid", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(0)
        self._layout.addWidget(grid)
        self.shelf_mode = False
        self.set_shelf_mode(False)

    def set_shelf_mode(self, enabled: bool) -> None:
        self.shelf_mode = enabled
        margin = round(_px(SHELF_FRAME)) if enabled else 0
        self._layout.setContentsMargins(margin, margin, margin, margin)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if not self.shelf_mode:
            return
        painter = QPainter(self)
        outer = QRectF(self.rect())
        frame = round(_px(SHELF_FRAME))
        inner = outer.adjusted(frame, frame, -frame, -frame)
        painter.fillRect(outer, QBrush(_wood(_FRAME, 512, 64, 23, strength=1.3)))
        # Bevels: the outer edge catches the light, the case's opening is in shade
        painter.setPen(QPen(QColor(255, 230, 200, 45), 1.0))
        painter.drawRect(outer.adjusted(0.5, 0.5, -0.5, -0.5))
        for step, alpha in ((1, 150), (3, 70), (6, 30)):
            painter.setPen(QPen(QColor(0, 0, 0, alpha), 2.0))
            painter.drawRect(inner.adjusted(-step, -step, step, step))
        painter.end()


class LibraryGrid(QListWidget):
    """The library's list view; in shelf mode it paints the bookcase and its games."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.shelf_mode = False
        # The box under the mouse lights up at once (_highlight_row) and slides
        # out once the mouse has rested on it (_hover_row)
        self._highlight_row = -1
        self._hover_row = -1
        self._active = -1
        # The selected box only stays out (and outlined) when the keyboard or
        # gamepad picked it; a box clicked with the mouse goes back on leaving
        self._show_current = True
        self._in_mouse_press = False
        #: How far the active box has slid out and turned, 0..1
        self.slide = 0.0
        self._slide_animation = QVariantAnimation(self)
        self._slide_animation.setStartValue(0.0)
        self._slide_animation.setEndValue(1.0)
        self._slide_animation.setDuration(SHELF_SLIDE_MS)
        self._slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide_animation.valueChanged.connect(self._on_slide_step)
        self.currentRowChanged.connect(self._on_current_row_changed)
        # A box only slides out once the mouse rests on it, so sweeping across
        # the shelf doesn't set every box in motion
        self._pending_hover = -1
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(SHELF_HOVER_DELAY_MS)
        self._hover_timer.timeout.connect(lambda: self._set_hover(self._pending_hover))

    def set_shelf_mode(self, enabled: bool) -> None:
        self.shelf_mode = enabled
        self._hover_row = -1
        self._highlight_row = -1
        # Boxes differ in thickness on the shelf; the cover grid's tiles do not
        self.setUniformItemSizes(not enabled)
        self._update_active()
        self.viewport().update()

    # -- The active box -----------------------------------------------------

    def active_row(self) -> int:
        """Row of the box slid out of the shelf: the one under the mouse, else the selected one."""
        if not self.shelf_mode:
            return -1
        if self._hover_row >= 0:
            row = self._hover_row
        else:
            row = self.currentRow() if self._show_current else -1
        return row if 0 <= row < self.count() else -1

    def _on_current_row_changed(self, _row: int) -> None:
        # Selected by the keyboard, gamepad or code rather than a click
        if not self._in_mouse_press:
            self._show_current = True
        self._update_active()

    def _update_active(self) -> None:
        active = self.active_row()
        if active != self._active:
            self._active = active
            # The new box slides out from the shelf; the previous one is back at once
            self._slide_animation.stop()
            self.slide = 0.0
            if active >= 0:
                self._slide_animation.start()
        self.viewport().update()

    def _on_slide_step(self, value: float) -> None:
        self.slide = float(value)
        self.viewport().update()

    def finish_slide(self) -> None:
        """Jump to the end of the slide-out animation."""
        self._slide_animation.stop()
        self.slide = 1.0 if self._active >= 0 else 0.0
        self.viewport().update()

    # -- Layout -------------------------------------------------------------

    def row_layout(self) -> dict[int, QRectF]:
        """Slots of the active box's row, after the boxes beside it made room.

        The boxes to the left of the active one keep their places and the ones
        to its right move along, first into free room at the end of the row,
        then by packing tighter. Only when there is nothing to its right do the
        boxes on its left close up instead. Either way the active slot still
        covers the spot Qt gave it, so the box under the mouse stays under it.
        """
        row = self._active
        if row < 0 or row >= self.count():
            return {}
        top = self.visualItemRect(self.item(row)).top()
        members = [r for r in range(self.count())
                   if self.visualItemRect(self.item(r)).top() == top]
        rects = {r: QRectF(self.visualItemRect(self.item(r))) for r in members}
        extra = ShelfDelegate.turned_width(rects[row].width(), self._aspect(row),
                                           self.slide) - rects[row].width()
        before = [r for r in members if r < row]
        after = [r for r in members if r > row]
        overflow = max(0.0, rects[members[-1]].right() + 1 + extra - self.viewport().width())
        squeeze_after = min(overflow, sum(rects[r].width() - 6 for r in after)) if after else 0.0
        squeeze_before = overflow - squeeze_after

        layout: dict[int, QRectF] = {}
        x = rects[members[0]].left()
        for member in members:
            width = rects[member].width()
            if member == row:
                width += extra
            elif member in after and squeeze_after:
                width -= squeeze_after / len(after)
            elif member in before and squeeze_before:
                width -= squeeze_before / len(before)
            layout[member] = QRectF(x, top, max(6.0, width), rects[member].height())
            x += max(6.0, width)
        return layout

    def _aspect(self, row: int) -> float:
        return float(self.item(row).data(ShelfDelegate.ASPECT_ROLE) or DEFAULT_ASPECT)

    def indexAt(self, point) -> QModelIndex:  # type: ignore[override]
        if not self.shelf_mode:
            return super().indexAt(point)
        layout = self.row_layout()
        for row, rect in layout.items():
            if rect.contains(QPointF(point)):
                return self.model().index(row, 0)
        index = super().indexAt(point)
        # A game of the active row is no longer where Qt laid it out
        return QModelIndex() if index.isValid() and index.row() in layout else index

    # -- Input --------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        # The keyboard (or gamepad) takes over from the mouse until it moves again
        self._hover_timer.stop()
        self._set_highlight(-1)
        self._set_hover(-1)
        self._show_current = True
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._show_current = False
        self._in_mouse_press = True
        try:
            super().mousePressEvent(event)
        finally:
            self._in_mouse_press = False
        self._update_active()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        super().mouseMoveEvent(event)
        if self.shelf_mode:
            row = self.indexAt(event.position().toPoint()).row()
            self._set_highlight(row)
            self._hover_soon(row)

    def viewportEvent(self, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.Leave:
            # Leaving the shelf puts the box back straight away
            self._hover_timer.stop()
            self._set_highlight(-1)
            self._set_hover(-1)
        elif event.type() == QEvent.Type.ToolTip and self.shelf_mode:
            # The caption on the plank already names the game: no tooltip on top
            event.ignore()
            return True
        return super().viewportEvent(event)

    def _hover_soon(self, row: int) -> None:
        """Make *row* the hovered box once the mouse has rested on it a moment."""
        if row == self._hover_row:
            self._hover_timer.stop()
        elif row != self._pending_hover or not self._hover_timer.isActive():
            # Restart only for a new box: small movements within one don't delay it
            self._pending_hover = row
            self._hover_timer.start()

    def _set_highlight(self, row: int) -> None:
        if row != self._highlight_row:
            self._highlight_row = row
            self.viewport().update()

    def _set_hover(self, row: int) -> None:
        if row != self._hover_row:
            self._hover_row = row
            self._update_active()

    # -- Painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if not self.shelf_mode:
            super().paintEvent(event)
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_bookcase(painter)

        delegate = self.itemDelegate()
        layout = self.row_layout()
        active = self._active
        visible = QRectF(self.viewport().rect())
        for row in range(self.count()):
            if row == active:
                continue  # painted last, in front of its neighbours
            rect = layout.get(row) or QRectF(self.visualItemRect(self.item(row)))
            if rect.intersects(visible):
                delegate.paint(painter, self._option(row, rect), self.model().index(row, 0))

        if active in layout and isinstance(delegate, ShelfDelegate):
            index = self.model().index(active, 0)
            depth = self.visualItemRect(self.item(active)).width() - 1
            delegate.paint_turned(painter, self._option(active, layout[active]), index,
                                  layout[active], self.slide, depth)
            delegate.paint_caption(painter, layout[active], visible, self.font(), index)
        painter.end()

    def _option(self, row: int, rect: QRectF) -> QStyleOptionViewItem:
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = rect.toAlignedRect()
        state = option.state
        index = self.model().index(row, 0)
        if self.selectionModel().isSelected(index):
            state |= QStyle.StateFlag.State_Selected
        if row == self._highlight_row:
            state |= QStyle.StateFlag.State_MouseOver
        if row == self.currentRow() and self.hasFocus() and self._show_current:
            state |= QStyle.StateFlag.State_HasFocus
        option.state = state
        return option

    def _row_tops(self) -> list[int]:
        """Top edge of every shelf row that shows in the viewport, in viewport pixels."""
        pitch = shelf_item_size().height()
        top = self.visualItemRect(self.item(0)).top() if self.count() else self.spacing()
        # Shelves continue above the first row (an empty one at the very top) and
        # below the last, so the case always fills the view
        while top > 0:
            top -= pitch
        while top + pitch < 0:
            top += pitch
        rows = []
        while top < self.viewport().height():
            rows.append(top)
            top += pitch
        return rows

    def _paint_bookcase(self, painter: QPainter) -> None:
        area = QRectF(self.viewport().rect())
        width = int(area.width())
        # Plain panelling at the back: only a faint grain, so it stays behind the boxes
        painter.fillRect(area, QBrush(_wood(_WALL, 512, 256, 3, strength=0.45)))

        plank_height = _plank_height()
        board_depth = _px(10)
        plank_grain = _wood(_PLANK, 512, plank_height, 11)
        top_grain = _wood(_PLANK_TOP, 512, round(_px(12)), 17)
        for row_top in self._row_tops():
            shelf_y = row_top + _shelf_offset()

            # Shade on the wall under the board above, where the light can't reach
            under = QLinearGradient(0, row_top, 0, row_top + _px(40))
            under.setColorAt(0, QColor(0, 0, 0, 110))
            under.setColorAt(1, QColor(0, 0, 0, 0))
            painter.fillRect(QRectF(0, row_top, width, _px(40)), under)

            # The board's top surface, seen receding behind the boxes
            board_top = QRectF(0, shelf_y - board_depth, width, board_depth)
            painter.setBrushOrigin(QPointF(0, board_top.top()))
            painter.fillRect(board_top, QBrush(top_grain))
            recede = QLinearGradient(0, board_top.top(), 0, board_top.bottom())
            recede.setColorAt(0, QColor(0, 0, 0, 90))
            recede.setColorAt(1, QColor(0, 0, 0, 0))
            painter.fillRect(board_top, recede)

            # The plank's front edge, lit from above
            front = QRectF(0, shelf_y, width, plank_height)
            painter.setBrushOrigin(QPointF(0, front.top()))
            painter.fillRect(front, QBrush(plank_grain))
            light = QLinearGradient(0, front.top(), 0, front.bottom())
            light.setColorAt(0, QColor(255, 255, 255, 34))
            light.setColorAt(0.25, QColor(255, 255, 255, 0))
            light.setColorAt(1, QColor(0, 0, 0, 70))
            painter.fillRect(front, light)
            painter.fillRect(QRectF(0, front.top(), width, 1), QColor(255, 235, 210, 60))
        painter.setBrushOrigin(QPointF(0, 0))


class ShelfDelegate(CoverTileDelegate):
    """Paints a game on the bookshelf: its spine, or the box turned to show its cover."""

    #: Item data role holding the box's edge colour (taken from the cover)
    SPINE_ROLE = Qt.ItemDataRole.UserRole + 92
    #: Item data role holding the rendered spine artwork (QImage)
    SPINE_IMAGE_ROLE = Qt.ItemDataRole.UserRole + 93
    #: Item data role holding the box front's height / width
    ASPECT_ROLE = Qt.ItemDataRole.UserRole + 94

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        size = index.data(Qt.ItemDataRole.SizeHintRole)
        return size if isinstance(size, QSize) and size.isValid() else shelf_item_size()

    @staticmethod
    def _shelf_y(rect: QRectF) -> float:
        return rect.top() + _shelf_offset()

    @staticmethod
    def _aspect(index) -> float:
        return float(index.data(ShelfDelegate.ASPECT_ROLE) or DEFAULT_ASPECT)

    @staticmethod
    def turned_width(slot_width: float, aspect: float, slide: float) -> float:
        """Width the slot needs for a box turned *slide* (0..1) of the way out."""
        angle = math.radians(SHELF_TILT * slide)
        front_width, _ = box_front_size(aspect)
        depth = slot_width - 1
        return (depth * math.cos(angle) + front_width * math.sin(angle) + 1
                + 2 * _px(SHELF_BOX_GAP) * slide)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        """A spine standing on the plank."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(option.rect)
        shelf_y = self._shelf_y(rect)
        _, height = box_front_size(self._aspect(index))
        highlighted = bool(option.state & QStyle.StateFlag.State_MouseOver)
        # Under the mouse the box is tipped up a little, as a finger would
        lift = _px(4.0) if highlighted else 0.0
        spine = QRectF(rect.left(), shelf_y - height - lift, rect.width() - 1.0, height)

        # The box stands a finger's width from the back wall: its shadow shows round it
        painter.setPen(Qt.PenStyle.NoPen)
        for grow, alpha in ((5, 18), (3, 30), (1, 45)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(spine.adjusted(-grow * 0.4, -grow, grow, 0), 2, 2)

        art = index.data(self.SPINE_IMAGE_ROLE)
        if isinstance(art, QImage) and not art.isNull():
            painter.drawImage(spine, art, QRectF(art.rect()))
        else:
            painter.fillRect(spine, QColor(index.data(self.SPINE_ROLE) or QColor(52, 56, 64)))
        self._shade_spine(painter, spine)
        accent = accent_color(self.parent())
        if highlighted:
            painter.fillRect(spine, QColor(255, 255, 255, 30))
            painter.setPen(QPen(_with_alpha(accent, 220), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(spine.adjusted(0.75, 0.75, -0.75, -0.75))

        if index.data(self.INSTALLED_ROLE):
            # A strip in the accent colour along the foot, like a shop's sticker
            painter.fillRect(QRectF(spine.left() + _px(3), spine.bottom() - _px(7),
                                    spine.width() - _px(6), _px(4)),
                             _with_alpha(accent, 235))

        # Contact shadow where the box stands on the plank
        painter.fillRect(QRectF(spine.left() - 1, shelf_y - 1, spine.width() + 3, 3),
                         QColor(0, 0, 0, 110))
        painter.restore()

    @staticmethod
    def _shade_spine(painter: QPainter, spine: QRectF) -> None:
        """Rounded card edges, light from above, and the lit top edge of the box."""
        across = QLinearGradient(spine.topLeft(), spine.topRight())
        across.setColorAt(0, QColor(0, 0, 0, 120))
        across.setColorAt(0.12, QColor(0, 0, 0, 0))
        across.setColorAt(0.5, QColor(255, 255, 255, 14))
        across.setColorAt(0.85, QColor(0, 0, 0, 0))
        across.setColorAt(1, QColor(0, 0, 0, 150))
        painter.fillRect(spine, across)
        down = QLinearGradient(spine.topLeft(), spine.bottomLeft())
        down.setColorAt(0, QColor(255, 255, 255, 30))
        down.setColorAt(0.2, QColor(255, 255, 255, 0))
        down.setColorAt(1, QColor(0, 0, 0, 70))
        painter.fillRect(spine, down)
        painter.fillRect(QRectF(spine.left() + 1, spine.top(), spine.width() - 2, 1.5),
                         QColor(255, 255, 255, 70))

    def paint_turned(self, painter: QPainter, option, index, rect: QRectF, slide: float,
                     depth: float) -> None:
        """The box slid half out of the shelf and turned, so its cover shows beside its spine.

        Seen from in front: the spine faces the viewer at an angle on the left,
        the cover recedes to the right in perspective. *slide* (0..1) is how far
        the box has come out and *depth* its thickness.
        """
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.initStyleOption(option, index)
        shelf_y = self._shelf_y(rect)
        angle = math.radians(SHELF_TILT * slide)
        front_width, height = box_front_size(self._aspect(index))

        # Nearer than its neighbours: its near corner a little taller, its foot over the plank
        near_height = height * (1 + 0.05 * slide)
        near_foot = shelf_y + _px(SHELF_PULL) * slide
        left = rect.left() + _px(SHELF_BOX_GAP) * slide
        near_x = left + depth * math.cos(angle)
        far_x = near_x + front_width * math.sin(angle)
        # The cover's far edge is deeper in the shelf: shorter and standing further back
        far_height = near_height * (1 - 0.12 * slide)
        far_foot = near_foot - _px(SHELF_PULL) * slide * 0.8
        spine_foot = near_foot - 1.5 * slide

        spine_quad = QPolygonF([QPointF(left, spine_foot - near_height * (1 - 0.01 * slide)),
                                QPointF(near_x, near_foot - near_height),
                                QPointF(near_x, near_foot), QPointF(left, spine_foot)])
        front_quad = QPolygonF([QPointF(near_x, near_foot - near_height),
                                QPointF(far_x, far_foot - far_height),
                                QPointF(far_x, far_foot), QPointF(near_x, near_foot)])

        self._paint_turned_shadow(painter, spine_quad, front_quad, shelf_y, slide)

        art = index.data(self.SPINE_IMAGE_ROLE)
        if isinstance(art, QImage) and not art.isNull():
            self._draw_on(painter, art, spine_quad)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(index.data(self.SPINE_ROLE) or QColor(52, 56, 64)))
            painter.drawPolygon(spine_quad)

        if far_x - near_x > 1:
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            pixmap = icon.pixmap(QSize(round(_px(SHELF_BOX_WIDTH * 2)),
                                       round(_px(SHELF_BOX_HEIGHT * 2)))) if (
                icon is not None and not icon.isNull()) else None
            if pixmap is not None and not pixmap.isNull():
                self._draw_on(painter, pixmap.toImage(), front_quad)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(46, 50, 58))
                painter.drawPolygon(front_quad)
            # Turned away from the light, and the plastic catching it along the fold
            painter.setPen(Qt.PenStyle.NoPen)
            fade = QLinearGradient(QPointF(near_x, 0), QPointF(far_x, 0))
            fade.setColorAt(0, QColor(255, 255, 255, int(40 * slide)))
            fade.setColorAt(0.15, QColor(0, 0, 0, int(25 * slide)))
            fade.setColorAt(1, QColor(0, 0, 0, int(110 * slide)))
            painter.setBrush(QBrush(fade))
            painter.drawPolygon(front_quad)

        # The spine faces the light as it turns towards the viewer
        painter.setPen(Qt.PenStyle.NoPen)
        lit = QLinearGradient(QPointF(left, 0), QPointF(near_x, 0))
        lit.setColorAt(0, QColor(0, 0, 0, 90))
        lit.setColorAt(0.2, QColor(0, 0, 0, 0))
        lit.setColorAt(1, QColor(255, 255, 255, int(20 * slide)))
        painter.setBrush(QBrush(lit))
        painter.drawPolygon(spine_quad)

        if index.data(self.INSTALLED_ROLE):
            self._paint_badge(painter, QRectF(near_x, near_foot - near_height,
                                              max(1.0, far_x - near_x), far_height).toRect(),
                              option.font, accent_color(self.parent()))
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(_with_alpha(accent_color(self.parent()), 235), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(spine_quad.united(front_quad))
        painter.restore()

    @staticmethod
    def _draw_on(painter: QPainter, image: QImage, quad: QPolygonF) -> None:
        """Map *image* onto *quad* (TL, TR, BR, BL) in perspective."""
        source = QPolygonF([QPointF(0, 0), QPointF(image.width(), 0),
                            QPointF(image.width(), image.height()), QPointF(0, image.height())])
        transform = QTransform()
        if not QTransform.quadToQuad(source, quad, transform):
            return
        painter.save()
        painter.setTransform(transform, True)
        painter.drawImage(0, 0, image)
        painter.restore()

    @staticmethod
    def _paint_turned_shadow(painter: QPainter, spine_quad: QPolygonF, front_quad: QPolygonF,
                             shelf_y: float, slide: float) -> None:
        """The pulled-out box shades the shelf and the boxes beside it."""
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        outline = spine_quad.united(front_quad).boundingRect()
        for step in (14, 9, 4):
            spread = step * slide
            painter.setBrush(QColor(0, 0, 0, int(36 * slide)))
            painter.drawRoundedRect(outline.adjusted(spread * 0.3, spread * 0.2, spread, -2), 3, 3)
        contact = QRectF(outline.left() - 2, shelf_y - 3, outline.width() + 10, 10 + 4 * slide)
        gradient = QLinearGradient(contact.topLeft(), contact.bottomLeft())
        gradient.setColorAt(0, QColor(0, 0, 0, 140))
        gradient.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(contact)
        painter.restore()

    def paint_caption(self, painter: QPainter, rect: QRectF, visible: QRectF,
                      base: QFont, index) -> None:
        """The active game's title and details on a card on the plank below it."""
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        meta = str(index.data(self.META_ROLE) or "")
        base = QFont(base)
        if base.pointSizeF() > 0:
            base.setPointSizeF(base.pointSizeF() * _scale)
        else:
            base.setPixelSize(max(1, round(_px(base.pixelSize()))))
        title_font = QFont(base)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_metrics, meta_metrics = QFontMetrics(title_font), QFontMetrics(base)
        gap = "   "
        max_width = min(visible.width() - 24, _px(520))
        meta_width = meta_metrics.horizontalAdvance(gap + meta) if meta else 0
        title = title_metrics.elidedText(title, Qt.TextElideMode.ElideRight,
                                         int(max_width - meta_width - 20))
        width = title_metrics.horizontalAdvance(title) + meta_width + 20

        shelf_y = self._shelf_y(rect)
        card = QRectF(0, shelf_y + _px(5), width, _plank_height() - _px(10))
        card.moveLeft(rect.center().x() - width / 2)
        # Keep the card on screen next to games at either end of the row
        card.moveLeft(min(max(card.left(), visible.left() + 8), visible.right() - 8 - width))

        accent = accent_color(self.parent())
        path = QPainterPath()
        path.addRoundedRect(card, 4, 4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillPath(path, _with_alpha(accent, 240))
        text = contrasting_text_color(accent)
        painter.setPen(text)
        painter.setFont(title_font)
        text_rect = card.adjusted(10, 0, -10, 0)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), title)
        if meta:
            painter.setPen(_with_alpha(text, 190))
            painter.setFont(base)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                             meta)
        painter.restore()
