"""Painting for the cover tiles in the native library grid.

Qt's default icon-mode item paints the label as a single run of plain text that
wraps to as many lines as it needs, which makes the grid rows ragged and the
long titles run into the tile below. This delegate takes over the whole tile:
it draws a rounded card, the cover with a soft drop shadow, the title clamped
to two lines and a muted metadata line, and it derives every colour from the
active palette so it follows the selected theme.
"""

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from ..config import COVER_TILE_HEIGHT, COVER_TILE_WIDTH
from ..utils import accent_color

# Padding around the card's content and the radius of its rounded corners
TILE_PADDING = 9
CARD_RADIUS = 10
COVER_RADIUS = 6

# Gap between the cover and the title, and between the title and the meta line
COVER_TEXT_GAP = 9
TITLE_META_GAP = 2

# Title lines a tile shows before the text is elided
TITLE_LINES = 2

META_POINT_DELTA = -1  # metadata sits one step smaller


def _title_font(base: QFont) -> QFont:
    """Return the font used for a tile's title."""
    font = QFont(base)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _meta_font(base: QFont) -> QFont:
    """Return the font used for a tile's metadata line."""
    font = QFont(base)
    size = font.pointSize()
    if size > 0:
        font.setPointSize(max(7, size + META_POINT_DELTA))
    else:
        font.setPixelSize(max(9, font.pixelSize() + META_POINT_DELTA))
    return font


def tile_size_hint(base: QFont) -> QSize:
    """Return the grid item size that fits a cover, two title lines and meta."""
    title_height = QFontMetrics(_title_font(base)).height() * TITLE_LINES
    meta_height = QFontMetrics(_meta_font(base)).height()
    height = (TILE_PADDING + COVER_TILE_HEIGHT + COVER_TEXT_GAP
              + title_height + TITLE_META_GAP + meta_height + TILE_PADDING)
    return QSize(COVER_TILE_WIDTH + 2 * TILE_PADDING, height)


class CoverTileDelegate(QStyledItemDelegate):
    """Draws a library grid item as a cover card with a clamped title."""

    #: Item data role holding the muted line under the title
    META_ROLE = Qt.ItemDataRole.UserRole + 90

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return tile_size_hint(option.font)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        # Take the option Qt would have styled with, minus the built-in
        # highlight — the card below paints selection and hover itself.
        opt = option
        self.initStyleOption(opt, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        state = opt.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        has_focus = bool(state & QStyle.StateFlag.State_HasFocus)

        rect = QRectF(opt.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        text_colour = opt.palette.color(opt.palette.ColorRole.WindowText)
        accent = accent_color(self.parent())

        self._paint_card(painter, rect, text_colour, accent, selected, hovered, has_focus)

        cover_rect = QRect(
            opt.rect.left() + TILE_PADDING, opt.rect.top() + TILE_PADDING,
            COVER_TILE_WIDTH, COVER_TILE_HEIGHT,
        )
        self._paint_cover(painter, cover_rect, opt, index, text_colour)

        text_left = opt.rect.left() + TILE_PADDING
        text_width = opt.rect.width() - 2 * TILE_PADDING
        text_top = cover_rect.bottom() + COVER_TEXT_GAP

        title_font = _title_font(opt.font)
        title_metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(text_colour if selected or hovered
                       else self._alpha(text_colour, 235))
        lines = self._wrap(str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                           title_metrics, text_width)
        for row, line in enumerate(lines):
            line_rect = QRect(text_left, text_top + row * title_metrics.height(),
                              text_width, title_metrics.height())
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignVCenter, line)

        meta = str(index.data(self.META_ROLE) or "")
        if meta:
            meta_font = _meta_font(opt.font)
            meta_metrics = QFontMetrics(meta_font)
            painter.setFont(meta_font)
            painter.setPen(self._alpha(text_colour, 140))
            meta_top = (text_top + TITLE_LINES * title_metrics.height()
                        + TITLE_META_GAP)
            painter.drawText(
                QRect(text_left, meta_top, text_width, meta_metrics.height()),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                meta_metrics.elidedText(meta, Qt.TextElideMode.ElideRight, text_width),
            )

        painter.restore()

    # ------------------------------------------------------------------
    # Pieces
    # ------------------------------------------------------------------

    def _paint_card(self, painter: QPainter, rect: QRectF, text_colour: QColor,
                    accent: QColor, selected: bool, hovered: bool,
                    has_focus: bool) -> None:
        """Fill the tile background and, when active, its accent outline."""
        path = QPainterPath()
        path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

        if selected:
            fill = self._alpha(accent, 55 if has_focus else 38)
        elif hovered:
            fill = self._alpha(text_colour, 22)
        else:
            fill = self._alpha(text_colour, 10)
        painter.fillPath(path, fill)

        if selected:
            painter.setPen(QPen(self._alpha(accent, 220), 1.6))
            painter.drawPath(path)
        elif hovered:
            painter.setPen(QPen(self._alpha(accent, 110), 1.2))
            painter.drawPath(path)

    def _paint_cover(self, painter: QPainter, box: QRect, opt, index,
                     text_colour: QColor) -> None:
        """Draw the artwork inside *box*: shadow, rounded art, sheen."""
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        pixmap = None
        if icon is not None and not icon.isNull():
            pixmap = icon.pixmap(box.size())

        if pixmap is None or pixmap.isNull():
            art = QRectF(box)
        else:
            width = min(pixmap.width(), box.width())
            height = min(pixmap.height(), box.height())
            art = QRectF(0, 0, width, height)
            art.moveCenter(QRectF(box).center())

        # Soft shadow: a couple of translucent rounded rects fanning downwards
        for step in (3, 2, 1):
            shadow = QRectF(art).adjusted(step, step + 1, step, step + 1)
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(shadow, COVER_RADIUS, COVER_RADIUS)
            painter.fillPath(shadow_path, QColor(0, 0, 0, 16))

        art_path = QPainterPath()
        art_path.addRoundedRect(art, COVER_RADIUS, COVER_RADIUS)
        painter.save()
        painter.setClipPath(art_path)
        if pixmap is None or pixmap.isNull():
            painter.fillRect(art, self._alpha(text_colour, 28))
        else:
            painter.drawPixmap(art.topLeft(), pixmap)
            # Faint top-down sheen so flat covers don't look pasted on
            sheen = QLinearGradient(QPointF(art.topLeft()), QPointF(art.bottomLeft()))
            sheen.setColorAt(0.0, QColor(255, 255, 255, 18))
            sheen.setColorAt(0.35, QColor(255, 255, 255, 0))
            sheen.setColorAt(1.0, QColor(0, 0, 0, 30))
            painter.fillRect(art, sheen)
        painter.restore()

        # Hairline edge to keep dark covers from bleeding into the card
        painter.setPen(QPen(self._alpha(text_colour, 40), 1.0))
        painter.drawPath(art_path)

    @staticmethod
    def _wrap(text: str, metrics: QFontMetrics, width: int) -> list[str]:
        """Greedily wrap *text* to at most ``TITLE_LINES`` lines, eliding the last."""
        elide = Qt.TextElideMode.ElideRight
        words = text.split()
        if not words:
            return []

        lines: list[str] = []
        current = words[0]
        index = 1
        while index < len(words):
            candidate = f"{current} {words[index]}"
            if metrics.horizontalAdvance(candidate) <= width:
                current = candidate
                index += 1
                continue
            if len(lines) + 1 == TITLE_LINES:
                # No room for another line: fold the rest into this one.
                rest = " ".join([current, *words[index:]])
                lines.append(metrics.elidedText(rest, elide, width))
                return lines
            lines.append(metrics.elidedText(current, elide, width))
            current = words[index]
            index += 1

        lines.append(metrics.elidedText(current, elide, width))
        return lines

    @staticmethod
    def _alpha(colour: QColor, alpha: int) -> QColor:
        """Return *colour* at the given alpha."""
        out = QColor(colour)
        out.setAlpha(alpha)
        return out
