"""The "insert the disc" animation played on the 3D box when a download starts.

The open box (see :mod:`.game_box`) holds a disc printed with the game's cover
art; :class:`DiscSequence` lifts it off the inside page, flies it into the
tray of a DVD drive drawn below the box, and closes the tray with the
activity LED blinking. It is pure painting: the view owns the clock, passes
the progress ``t`` (0..1) and the on-screen quad of the page the disc rests
on, and the sequence draws the disc and the drive for that moment.

Timeline (fractions of :data:`~gameyfin_frontend.config.BOX_DISC_MS`)::

    0.00-0.22  box opens and pulls back, drive fades in
    0.20-0.32  disc appears on the page
    0.30-0.42  tray ejects; disc lifts off
    0.40-0.66  disc flies to the tray, spinning and tilting flat
    0.66-0.72  disc settles
    0.72-0.84  tray closes with the disc
    0.84-1.00  LED blinks, then everything settles back
"""

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QConicalGradient, QFont, QFontMetricsF, QImage,
                         QLinearGradient, QPainter, QPainterPath, QPen,
                         QPolygonF, QRadialGradient)

from ..services.gameyfin_api import Game


def _phase(t: float, start: float, end: float) -> float:
    """Progress of *t* through [start, end], clamped and smoothstepped."""
    if t <= start:
        return 0.0
    if t >= end:
        return 1.0
    x = (t - start) / (end - start)
    return x * x * (3 - 2 * x)


def _lerp(a: float, b: float, x: float) -> float:
    return a + (b - a) * x


def _bezier(p0: QPointF, p1: QPointF, p2: QPointF, p3: QPointF, x: float) -> QPointF:
    u = 1 - x
    return p0 * (u ** 3) + p1 * (3 * u * u * x) + p2 * (3 * u * x * x) + p3 * (x ** 3)


def render_disc(game: Game, cover: QImage | None, size: int, base: QColor) -> QImage:
    """A printed game disc: cover art on the label, clear hub, shiny rim."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    centre = QPointF(size / 2, size / 2)
    radius = size / 2 * 0.98

    def circle(r: float) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(centre, r, r)
        return path

    # Silver data side peeking out around the label, with a rainbow sheen
    sheen = QConicalGradient(centre, 30)
    for stop, colour in ((0.0, "#d8dde3"), (0.12, "#f4e7ff"), (0.25, "#b9c2cc"),
                         (0.4, "#e6fff4"), (0.55, "#c9d0d8"), (0.7, "#fff4dc"),
                         (0.85, "#b3bcc6"), (1.0, "#d8dde3")):
        sheen.setColorAt(stop, QColor(colour))
    painter.fillPath(circle(radius), sheen)

    # Printed label
    label = circle(radius * 0.95).subtracted(circle(radius * 0.34))
    painter.save()
    painter.setClipPath(label)
    art_rect = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
    if cover is not None and not cover.isNull():
        scale = max(art_rect.width() / cover.width(), art_rect.height() / cover.height())
        src_w, src_h = art_rect.width() / scale, art_rect.height() / scale
        # Favour the top of the cover, where key art and logos usually are
        source = QRectF((cover.width() - src_w) / 2, 0, src_w, src_h)
        painter.drawImage(art_rect, cover, source)
    else:
        fill = QLinearGradient(art_rect.topLeft(), art_rect.bottomRight())
        fill.setColorAt(0, base.lighter(160))
        fill.setColorAt(1, base.darker(200))
        painter.fillRect(art_rect, fill)
    shade = QLinearGradient(0, centre.y(), 0, centre.y() + radius)
    shade.setColorAt(0, QColor(0, 0, 0, 0))
    shade.setColorAt(1, QColor(0, 0, 0, 190))
    painter.fillRect(art_rect, shade)

    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(1, int(radius * 0.1)))
    painter.setFont(font)
    title_rect = QRectF(centre.x() - radius * 0.6, centre.y() + radius * 0.52,
                        radius * 1.2, radius * 0.16)
    painter.setPen(QColor(255, 255, 255, 235))
    painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter),
                     QFontMetricsF(font).elidedText(game.title, Qt.TextElideMode.ElideRight,
                                                    title_rect.width()))
    small = QFont(font)
    small.setPixelSize(max(1, int(radius * 0.06)))
    small.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
    painter.setFont(small)
    painter.setPen(QColor(255, 255, 255, 190))
    painter.drawText(QRectF(centre.x() - radius * 0.5, centre.y() + radius * 0.69, radius, radius * 0.1),
                     int(Qt.AlignmentFlag.AlignCenter), "DISC 1")
    painter.restore()

    # Clear plastic hub with its stacking ring, then the hole
    painter.fillPath(circle(radius * 0.34).subtracted(circle(radius * 0.12)), QColor(225, 230, 236, 150))
    painter.setPen(QPen(QColor(255, 255, 255, 120), max(1.0, radius * 0.012)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(centre, radius * 0.25, radius * 0.25)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.fillPath(circle(radius * 0.12), Qt.GlobalColor.transparent)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    painter.setPen(QPen(QColor(255, 255, 255, 170), max(1.0, radius * 0.01)))
    painter.drawEllipse(centre, radius, radius)
    painter.end()
    return image


class DiscSequence:
    """Draws the disc and the drive for one moment of the insert animation."""

    def __init__(self, disc: QImage) -> None:
        self.disc = disc

    @staticmethod
    def stage(t: float) -> float:
        """How far the box is pulled back (0..1) to make room for the drive."""
        return _phase(t, 0.0, 0.22) * (1 - _phase(t, 0.9, 1.0))

    @staticmethod
    def _drive_rect(width: float, height: float) -> QRectF:
        drive_w = min(width * 0.56, 460.0)
        drive_h = drive_w * 0.17
        return QRectF((width - drive_w) / 2, height * 0.78, drive_w, drive_h)

    def paint(self, painter: QPainter, t: float, width: float, height: float,
              page: QPolygonF | None, text_colour: QColor) -> None:
        drive = self._drive_rect(width, height)
        slot = QRectF(drive.x() + drive.width() * 0.08, drive.y() + drive.height() * 0.3,
                      drive.width() * 0.84, drive.height() * 0.1)
        tray_out = _phase(t, 0.30, 0.42) * (1 - _phase(t, 0.72, 0.84))
        tray_depth = drive.height() * 1.55
        # Where the disc sits on a fully ejected tray, and its size there
        well = QPointF(slot.center().x(), slot.top() + tray_depth * 0.55)
        well_r = slot.width() * 0.34
        squash_flat = 0.3

        drive_alpha = _phase(t, 0.08, 0.22) * (1 - _phase(t, 0.92, 1.0))
        if drive_alpha > 0:
            painter.save()
            painter.setOpacity(drive_alpha)
            self._paint_drive(painter, drive, slot, t, text_colour)
            if tray_out > 0:
                self._paint_tray(painter, slot, tray_depth, tray_out, well_r)
            painter.restore()

        # -- The disc --------------------------------------------------------
        appear = _phase(t, 0.20, 0.32)
        if appear <= 0 or t >= 0.9:
            return
        if page is not None and not page.isEmpty():
            bounds = page.boundingRect()
            start = bounds.center()
            start_r = min(bounds.width(), bounds.height()) * 0.42
        else:
            start, start_r = QPointF(width / 2, height * 0.35), height * 0.18
        lift = _phase(t, 0.32, 0.40)
        lifted = QPointF(start.x(), start.y() - height * 0.04 * lift)
        fly = _phase(t, 0.40, 0.66)
        settle = _phase(t, 0.66, 0.72)

        # The well is fixed to the tray, so the disc rides it back into the slot
        end = QPointF(well.x(), well.y() - tray_depth * (1 - tray_out))
        if fly < 1:
            control1 = QPointF(lifted.x(), lifted.y() - height * 0.12)
            control2 = QPointF(end.x(), end.y() - height * 0.32)
            centre = _bezier(lifted, control1, control2, end, fly)
        else:
            # A small bounce as it drops into the well
            centre = QPointF(end.x(), end.y() - math.sin(settle * math.pi) * drive.height() * 0.12)
        radius = _lerp(start_r * (0.92 + 0.08 * appear) * (1 + 0.05 * lift), well_r, fly)
        squash = _lerp(1.0, squash_flat, _phase(t, 0.48, 0.66))
        spin = 720 * _phase(t, 0.36, 0.72) + 260 * _phase(t, 0.72, 0.84)

        painter.save()
        painter.setOpacity(appear)
        if t >= 0.66:
            # On the tray: only the part still out of the slot is visible
            bottom = height if t < 0.72 else slot.top() + tray_depth * tray_out
            painter.setClipRect(QRectF(0, slot.top(), width, max(0.0, bottom - slot.top())))
        shadow_offset = height * 0.035 * lift * (1 - fly) + drive.height() * 0.05
        shadow = QRadialGradient(QPointF(0, 0), 1.0)
        shadow.setColorAt(0, QColor(0, 0, 0, int(110 * (0.4 + 0.6 * lift))))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.save()
        painter.translate(centre.x(), centre.y() + shadow_offset)
        painter.scale(radius * 1.08, radius * squash * 1.08)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QPointF(0, 0), 1.0, 1.0)
        painter.restore()

        painter.translate(centre)
        painter.scale(1.0, squash)
        painter.rotate(spin)
        size = 2 * radius
        painter.drawImage(QRectF(-radius, -radius, size, size), self.disc)
        painter.restore()

    @staticmethod
    def _paint_drive(painter: QPainter, drive: QRectF, slot: QRectF, t: float,
                     text_colour: QColor) -> None:
        """A 5.25" optical drive front: bezel, slot, LED and eject button."""
        bezel = QLinearGradient(drive.topLeft(), drive.bottomLeft())
        bezel.setColorAt(0, QColor(58, 61, 68))
        bezel.setColorAt(0.5, QColor(36, 38, 43))
        bezel.setColorAt(1, QColor(24, 25, 29))
        path = QPainterPath()
        path.addRoundedRect(drive, drive.height() * 0.12, drive.height() * 0.12)
        painter.fillPath(path, bezel)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1.0))
        painter.drawLine(QPointF(drive.left() + 4, drive.top() + 1), QPointF(drive.right() - 4, drive.top() + 1))

        painter.fillRect(slot, QColor(6, 6, 8))
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
        painter.drawLine(slot.bottomLeft(), slot.bottomRight())

        unit = drive.height()
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(1, int(unit * 0.16)))
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 120))
        painter.drawText(QRectF(slot.left(), drive.top() + unit * 0.55, slot.width() * 0.5, unit * 0.3),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), "DVD-ROM")

        # Activity LED: steady amber while the tray moves, blinking green once loaded
        led = QPointF(slot.left() + slot.width() * 0.62, drive.top() + unit * 0.7)
        if t >= 0.84:
            on = math.sin((t - 0.84) * 70) > -0.2
            colour = QColor(90, 255, 120) if on else QColor(30, 70, 40)
        elif t >= 0.30:
            colour = QColor(255, 180, 60)
        else:
            colour = QColor(60, 50, 40)
        glow = QRadialGradient(led, unit * 0.22)
        glow.setColorAt(0, QColor(colour.red(), colour.green(), colour.blue(), 180))
        glow.setColorAt(1, QColor(colour.red(), colour.green(), colour.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(led, unit * 0.22, unit * 0.22)
        painter.setBrush(colour)
        painter.drawEllipse(led, unit * 0.05, unit * 0.05)

        button = QRectF(slot.right() - slot.width() * 0.14, drive.top() + unit * 0.6,
                        slot.width() * 0.14, unit * 0.2)
        button_path = QPainterPath()
        button_path.addRoundedRect(button, unit * 0.06, unit * 0.06)
        pressed = 0.30 <= t < 0.34
        painter.fillPath(button_path, QColor(20, 21, 24) if pressed else QColor(70, 73, 80))

        if t >= 0.72:
            caption = QFont()
            caption.setPixelSize(max(1, int(unit * 0.22)))
            painter.setFont(caption)
            colour = QColor(text_colour)
            colour.setAlpha(int(200 * _phase(t, 0.72, 0.8)))
            painter.setPen(colour)
            painter.drawText(QRectF(drive.left(), drive.bottom() + unit * 0.15, drive.width(), unit * 0.4),
                             int(Qt.AlignmentFlag.AlignCenter), "Downloading…")

    @staticmethod
    def _paint_tray(painter: QPainter, slot: QRectF, full_depth: float, tray_out: float,
                    well_r: float) -> None:
        """The tray sliding out of the slot towards the viewer."""
        depth = full_depth * tray_out
        spread = slot.width() * 0.04 * tray_out
        tray = QPolygonF([
            slot.topLeft(), slot.topRight(),
            QPointF(slot.right() + spread, slot.top() + depth),
            QPointF(slot.left() - spread, slot.top() + depth),
        ])
        fill = QLinearGradient(0, slot.top(), 0, slot.top() + max(depth, 1.0))
        fill.setColorAt(0, QColor(30, 31, 35))
        fill.setColorAt(1, QColor(66, 69, 76))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPolygon(tray)
        # Front lip of the tray
        lip_h = max(2.0, slot.height() * 1.4)
        painter.fillRect(QRectF(slot.left() - spread, slot.top() + depth - lip_h,
                                slot.width() + 2 * spread, lip_h), QColor(48, 50, 56))
        # The recessed well the disc drops into, sliding with the tray
        well_centre = QPointF(slot.center().x(), slot.top() + depth - full_depth * 0.45)
        painter.save()
        painter.setClipRect(QRectF(slot.left() - spread, slot.top(), slot.width() + 2 * spread, depth))
        painter.setBrush(QColor(18, 19, 22))
        painter.drawEllipse(well_centre, well_r * 1.04, well_r * 0.3 * 1.04)
        painter.restore()
