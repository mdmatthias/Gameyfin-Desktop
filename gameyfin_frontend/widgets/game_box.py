"""A game shown as a physical 3D "big box", the way it would sit on a shelf.

The box is a perspective-projected cuboid painted with plain QPainter (no
OpenGL): every visible face is an image mapped onto its projected quad with
``QTransform.quadToQuad``. Because the box is convex, back-face culling alone
gives a correct picture — no depth sorting needed.

* front: the game's cover art
* back: a generated "back of the box" (hero art, blurb, screenshots, an info
  strip with ratings, credits and a barcode) — see :func:`render_back_cover`
* spines: the title on a band in the cover's own edge colour
* inside: the front cover is hinged at the spine and opens like a book (think
  of the old World of Warcraft box), revealing a fact sheet on the inside of
  the cover and the full, scrollable description on the page beneath it —
  see :func:`render_inner_flap` and :class:`DescriptionPage`

Drag to turn the box. Clicking the cover (or Space/Return, which is what the
gamepad's A sends) opens it, clicking outside the page closes it again;
clicking the back turns the box over. Clicking a screenshot on the back emits
:attr:`GameBoxView.screenshot_clicked`, and the "read more" under a cut-short
blurb opens the cover. :meth:`GameBoxView.insert_disc` plays the download
animation from :mod:`.game_disc`: the box opens and its disc goes into a drive.
"""

import math
import random
import re
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import (QDate, QEasingCurve, QLocale, QPointF, QRectF, Qt,
                          QTimer, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage,
                         QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPalette,
                         QPainterPath, QPen, QPolygonF, QRadialGradient,
                         QTransform)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..config import (BOX_BACK_TEXTURE_WIDTH, BOX_DEPTH_RATIO, BOX_DEFAULT_ASPECT,
                      BOX_DISC_MS, BOX_FLIP_MS, BOX_OPEN_ANGLE, BOX_OPEN_MS, BOX_OPEN_PITCH,
                      BOX_OPEN_TURN_FIRST, BOX_OPEN_YAW, BOX_REST_PITCH, BOX_REST_YAW)
from ..services.gameyfin_api import Game
from ..utils import format_size
from .game_disc import DiscSequence

# Light direction in view space (from upper left, towards the viewer)
_LIGHT = (-0.45, 0.55, 0.70)
_LIGHT_LEN = math.sqrt(sum(c * c for c in _LIGHT))
_LIGHT = tuple(c / _LIGHT_LEN for c in _LIGHT)

# Pointer travel (px) below which a press/release counts as a click, not a drag
_CLICK_SLOP = 5
_YAW_PER_PX = 0.45
_PITCH_PER_PX = 0.25
_PITCH_LIMIT = 30.0


# ------------------------------------------------------------------
# Textures
# ------------------------------------------------------------------

_grain: QImage | None = None


def _grain_tile() -> QImage:
    """A small tile of print/paper grain: faint light and dark specks."""
    global _grain
    if _grain is None:
        rng = random.Random(7)  # fixed, so the grain does not crawl between renders
        size = 128
        _grain = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        _grain.fill(Qt.GlobalColor.transparent)
        for y in range(size):
            for x in range(size):
                alpha = int(rng.random() ** 2 * 22)
                value = 255 if rng.random() < 0.5 else 0
                _grain.setPixelColor(x, y, QColor(value, value, value, alpha))
    return _grain


def _finish_texture(image: QImage, name: str) -> QImage:
    """Return a copy of a face texture printed on cardboard.

    Adds paper grain, the soft fall-off of a studio light from above, and darkened
    edges where the cardboard rounds over a fold. Done once per texture size, so it
    costs nothing while the box animates.
    """
    finished = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    width, height = finished.width(), finished.height()
    rect = QRectF(0, 0, width, height)
    painter = QPainter(finished)
    # Inside the cover the dark, matte card would turn the grain into speckles
    painter.setOpacity(0.5 if name == "inside" else 1.0)
    painter.fillRect(rect, QBrush(_grain_tile()))
    painter.setOpacity(1.0)

    if name != "inside":
        falloff = QLinearGradient(0, 0, 0, height)
        falloff.setColorAt(0, QColor(255, 255, 255, 16))
        falloff.setColorAt(0.35, QColor(255, 255, 255, 0))
        falloff.setColorAt(1, QColor(0, 0, 0, 30))
        painter.fillRect(rect, falloff)

    # The same edge width on every face, relative to the box's size
    edge = max(1.0, max(width, height) * 0.006)
    for start, end, strip in (
        (QPointF(0, 0), QPointF(edge, 0), QRectF(0, 0, edge, height)),
        (QPointF(width, 0), QPointF(width - edge, 0), QRectF(width - edge, 0, edge, height)),
        (QPointF(0, 0), QPointF(0, edge), QRectF(0, 0, width, edge)),
        (QPointF(0, height), QPointF(0, height - edge), QRectF(0, height - edge, width, edge)),
    ):
        rounding = QLinearGradient(start, end)
        rounding.setColorAt(0, QColor(0, 0, 0, 120))
        rounding.setColorAt(1, QColor(0, 0, 0, 0))
        painter.fillRect(strip, rounding)
    painter.end()
    return finished


def render_cap(width: int, height: int, base: QColor) -> QImage:
    """The top or bottom of the box: plain card in the spine colour."""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, width, 0)
    gradient.setColorAt(0, base.darker(125))
    gradient.setColorAt(0.5, base.darker(105))
    gradient.setColorAt(1, base.darker(125))
    painter.fillRect(image.rect(), gradient)
    painter.end()
    return image


def edge_color(cover: QImage | None) -> QColor:
    """Return a spine colour: the average of the cover's left edge, kept dark
    enough for white text to stay readable on it."""
    if cover is None or cover.isNull():
        return QColor(40, 44, 52)
    strip = cover.copy(0, 0, max(1, cover.width() // 14), cover.height())
    pixel = strip.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
    hue, sat, light, _ = pixel.getHsl()
    return QColor.fromHsl(max(hue, 0), sat, min(light, 105))


def _plain_text(summary: str | None) -> str:
    """Server summaries may carry HTML; the box back wants plain prose."""
    if not summary:
        return ""
    text = re.sub(r"<br\s*/?>|</p>", "\n", summary, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\n{2,}", "\n", text).strip()


def _format_release(release: str | None) -> str:
    """Show the release date in the user's locale, falling back to the raw value."""
    if not release:
        return ""
    date = QDate.fromString(release[:10], Qt.DateFormat.ISODate)
    if not date.isValid():
        return release
    return QLocale().toString(date, QLocale.FormatType.ShortFormat)


def _font(px: float, bold: bool = False, letter_spacing: float = 0) -> QFont:
    font = QFont()
    font.setPixelSize(max(1, int(px)))
    font.setBold(bold)
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return font


def _font_to_fit(text: str, max_px: float, width: float, bold: bool = False,
                 letter_spacing: float = 0, lines: int = 1) -> QFont:
    """Return the largest font up to *max_px* that fits *text* in *width*.

    With ``lines > 1`` the text may word-wrap onto that many lines (draw it with
    ``TextWordWrap``); words are never broken.
    """
    def fits(font: QFont) -> bool:
        metrics = QFontMetricsF(font)
        if lines == 1:
            return metrics.horizontalAdvance(text) <= width
        if max(metrics.horizontalAdvance(word) for word in text.split() or [""]) > width:
            return False
        wrapped = metrics.boundingRect(QRectF(0, 0, width, 1e6), int(Qt.TextFlag.TextWordWrap), text)
        return round(wrapped.height() / metrics.lineSpacing()) <= lines

    px = max_px
    if lines == 1:
        # Jump straight to the proportional size instead of stepping down to it
        natural = QFontMetricsF(_font(px, bold, letter_spacing)).horizontalAdvance(text)
        if natural > width:
            px *= width / natural
    font = _font(px, bold, letter_spacing)
    # _font rounds to whole pixels (and spacing does not scale), so it can still overshoot
    while px > 1 and not fits(font):
        px -= 1
        font = _font(px, bold, letter_spacing)
    return font


def _fit_text(text: str, font: QFont, rect: QRectF) -> str:
    """Trim *text* at a word boundary (adding an ellipsis) until it fits *rect*."""
    metrics = QFontMetricsF(font)
    flags = int(Qt.TextFlag.TextWordWrap)

    def fits(candidate: str) -> bool:
        return metrics.boundingRect(rect, flags, candidate).height() <= rect.height()

    if fits(text):
        return text
    words = text.split(" ")
    low, high = 0, len(words)
    while low < high:
        mid = (low + high + 1) // 2
        if fits(" ".join(words[:mid]) + "…"):
            low = mid
        else:
            high = mid - 1
    return (" ".join(words[:low]).rstrip(",.;: ") + "…") if low else ""


def _draw_cropped(painter: QPainter, image: QImage, target: QRectF) -> None:
    """Fill *target* with *image*, cropping the overflow (CSS ``cover``)."""
    if image.isNull() or target.isEmpty():
        return
    scale = max(target.width() / image.width(), target.height() / image.height())
    src_w, src_h = target.width() / scale, target.height() / scale
    source = QRectF((image.width() - src_w) / 2, (image.height() - src_h) / 2, src_w, src_h)
    painter.drawImage(target, image, source)


def render_front_placeholder(game: Game, width: int, height: int, base: QColor) -> QImage:
    """A plain front cover for games the server has no cover art for."""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0, base.lighter(150))
    gradient.setColorAt(1, base.darker(220))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(QColor(255, 255, 255, 235))
    painter.setFont(_font(width * 0.11, bold=True))
    margin = width * 0.1
    painter.drawText(QRectF(margin, 0, width - 2 * margin, height),
                     int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), game.title)
    painter.end()
    return image


def render_spine(game: Game, width: int, height: int, base: QColor) -> QImage:
    """The narrow side of the box: title running top to bottom on a colour band."""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    gradient = QLinearGradient(0, 0, width, 0)
    gradient.setColorAt(0, base.darker(135))
    gradient.setColorAt(0.5, base)
    gradient.setColorAt(1, base.darker(135))
    painter.fillRect(image.rect(), gradient)

    # Accent bands at the ends, like a publisher's series livery
    band = height * 0.045
    painter.fillRect(QRectF(0, 0, width, band), QColor(0, 0, 0, 120))
    painter.fillRect(QRectF(0, height - band, width, band), QColor(0, 0, 0, 120))

    painter.translate(width, 0)
    painter.rotate(90)
    # Now x runs down the spine and y across it (0 .. width)
    length = height
    # "PC (Microsoft Windows)" -> "PC": a real box only prints the short name
    platform = (game.platforms[0] if game.platforms else "PC").split("(")[0].strip().upper() or "PC"
    # The platform sits in the top end, the title fills the rest. Long names
    # (NINTENDO ENTERTAINMENT SYSTEM) get more room so they stay readable.
    readable = QFontMetricsF(_font(width * 0.12, bold=True, letter_spacing=1)).horizontalAdvance(platform)
    platform_len = min(max(length * 0.16, readable + band * 0.6), length * 0.35)
    platform_end = band + platform_len
    platform_rect = QRectF(band * 1.4, 0, platform_end - band * 1.4, width)
    # Shrink what still does not fit rather than cut it
    platform_font = _font_to_fit(platform, width * 0.17, platform_rect.width(),
                                 bold=True, letter_spacing=1)
    painter.setFont(platform_font)
    painter.setPen(QColor(255, 255, 255, 190))
    painter.drawText(platform_rect, int(Qt.AlignmentFlag.AlignCenter), platform)
    painter.fillRect(QRectF(platform_end, width * 0.3, max(1.0, width * 0.02), width * 0.4),
                     QColor(255, 255, 255, 90))

    title_rect = QRectF(platform_end + length * 0.03, 0,
                        length - band * 1.5 - platform_end - length * 0.03, width)
    # Long titles get a smaller font instead of an ellipsis
    title = game.title.upper()
    title_font = _font_to_fit(title, width * 0.4, title_rect.width(), bold=True)
    painter.setFont(title_font)
    painter.setPen(QColor(255, 255, 255, 240))
    painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), title)
    painter.end()
    return image


@dataclass
class BackCover:
    """A rendered back cover plus where its clickable parts sit (normalised 0..1)."""

    image: QImage
    screenshot_rects: list[QRectF]
    # Set only when the blurb was cut short, so there is more to read
    description_rect: QRectF | None = None


def render_back_cover(game: Game, width: int, height: int, base: QColor,
                      hero: QImage | None, screenshots: list[QImage]) -> BackCover:
    """Lay out the back of the box like a retail release would."""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    background = QLinearGradient(0, 0, 0, height)
    hero_h = height * 0.27
    # Flat behind the hero so its fade-out meets the background seamlessly
    background.setColorAt(0, base.darker(160))
    background.setColorAt(0.27, base.darker(160))
    background.setColorAt(1, QColor(12, 12, 16))
    painter.fillRect(image.rect(), background)

    unit = width / 100.0  # layout unit: 1% of the box width
    margin = 5 * unit
    inner_w = width - 2 * margin

    # -- Hero art fading into the background, with the title over it --------
    if hero is not None and not hero.isNull():
        _draw_cropped(painter, hero, QRectF(0, 0, width, hero_h))
    fade = QLinearGradient(0, hero_h * 0.35, 0, hero_h)
    transparent = QColor(base.darker(160))
    transparent.setAlpha(0)
    fade.setColorAt(0, transparent)
    fade.setColorAt(1, base.darker(160))
    painter.fillRect(QRectF(0, 0, width, hero_h + 1), fade)

    title = game.title
    # Long titles wrap onto a second line, and only shrink when that is not enough
    title_font = _font_to_fit(title, 7.2 * unit, inner_w, bold=True, lines=2)
    # Bottom-aligned, so a second line grows up into the hero art
    title_rect = QRectF(margin, hero_h - 26 * unit, inner_w, 24 * unit)
    title_flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap)
    painter.setFont(title_font)
    painter.setPen(QColor(0, 0, 0, 160))
    painter.drawText(title_rect.translated(0.4 * unit, 0.4 * unit), title_flags, title)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(title_rect, title_flags, title)

    y = hero_h + 1.5 * unit

    # -- Genre / theme chips -------------------------------------------------
    chips = (game.genres + game.themes)[:6]
    if chips:
        chip_font = _font(2.6 * unit, bold=True, letter_spacing=0.5)
        metrics = QFontMetricsF(chip_font)
        painter.setFont(chip_font)
        x = margin
        chip_h = 4.6 * unit
        for chip in chips:
            text = chip.upper()
            chip_w = metrics.horizontalAdvance(text) + 3 * unit
            if x + chip_w > width - margin:
                break
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y, chip_w, chip_h), chip_h / 2, chip_h / 2)
            painter.fillPath(path, QColor(255, 255, 255, 34))
            painter.setPen(QColor(255, 255, 255, 215))
            painter.drawText(QRectF(x, y, chip_w, chip_h), int(Qt.AlignmentFlag.AlignCenter), text)
            x += chip_w + 1.5 * unit
        y += chip_h + 2.5 * unit

    # -- Info strip at the bottom (laid out first; the middle takes the rest) --
    strip_h = height * 0.15
    strip_top = height - strip_h

    # -- Blurb ---------------------------------------------------------------
    blurb = _plain_text(game.summary) or "No description available."
    blurb_font = _font(3.0 * unit)
    shots = [shot for shot in screenshots if not shot.isNull()][:4]
    # Without screenshots the blurb may run all the way down to the strip
    blurb_limit = (height * 0.2) if shots else (strip_top - y - 3 * unit)
    more_font = _font(2.5 * unit, bold=True, letter_spacing=1)
    more_h = QFontMetricsF(more_font).height() + 1.2 * unit
    blurb_rect = QRectF(margin, y, inner_w, blurb_limit)
    full = blurb
    blurb = _fit_text(full, blurb_font, blurb_rect)
    truncated = blurb != full
    if truncated:
        # Make room for the "read more" line under the text
        blurb_rect.setHeight(max(0.0, blurb_limit - more_h))
        blurb = _fit_text(full, blurb_font, blurb_rect)
    painter.setFont(blurb_font)
    painter.setPen(QColor(235, 235, 240, 225))
    used = QFontMetricsF(blurb_font).boundingRect(
        blurb_rect, int(Qt.TextFlag.TextWordWrap), blurb).height()
    painter.drawText(blurb_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                                     | Qt.TextFlag.TextWordWrap), blurb)
    description_rect = None
    if truncated:
        more_rect = QRectF(margin, y + used + 0.6 * unit, inner_w, more_h)
        painter.setFont(more_font)
        painter.setPen(base.lighter(260) if base.lightness() > 20 else QColor(255, 255, 255))
        painter.drawText(more_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         "READ MORE ›")
        used += 0.6 * unit + more_h
        description_rect = QRectF(margin / width, y / height, inner_w / width, used / height)
    y += used + 3.5 * unit

    # -- Screenshot grid -----------------------------------------------------
    rects: list[QRectF] = []
    if shots:
        columns = 1 if len(shots) == 1 else 2
        rows = math.ceil(len(shots) / columns)
        gap = 2.5 * unit
        available_h = strip_top - y - 3 * unit
        cell_w = (inner_w - gap * (columns - 1)) / columns
        cell_h = cell_w * 9 / 16
        if rows * cell_h + (rows - 1) * gap > available_h:
            cell_h = max(0.0, (available_h - (rows - 1) * gap) / rows)
            cell_w = cell_h * 16 / 9
        grid_w = columns * cell_w + (columns - 1) * gap
        left = (width - grid_w) / 2
        for index, shot in enumerate(shots):
            row, column = divmod(index, columns)
            cell = QRectF(left + column * (cell_w + gap), y + row * (cell_h + gap), cell_w, cell_h)
            if cell.height() < 4 * unit:
                break
            painter.fillRect(cell.translated(0.6 * unit, 0.8 * unit), QColor(0, 0, 0, 120))
            _draw_cropped(painter, shot, cell)
            painter.setPen(QPen(QColor(255, 255, 255, 200), max(1.0, 0.35 * unit)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(cell)
            rects.append(QRectF(cell.x() / width, cell.y() / height,
                                cell.width() / width, cell.height() / height))

    # -- Info strip ----------------------------------------------------------
    painter.fillRect(QRectF(0, strip_top, width, strip_h), QColor(8, 8, 10, 235))
    painter.fillRect(QRectF(0, strip_top, width, 0.5 * unit), base.lighter(140))

    pad = 3 * unit
    badge_d = strip_h - 2 * pad
    x = margin
    rating = game.user_rating if game.user_rating is not None else game.critic_rating
    if rating is not None:
        badge = QRectF(x, strip_top + pad, badge_d, badge_d)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(badge)
        painter.setPen(QPen(QColor(20, 20, 24), 0.8 * unit))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(badge.adjusted(1.4 * unit, 1.4 * unit, -1.4 * unit, -1.4 * unit))
        painter.setPen(QColor(20, 20, 24))
        painter.setFont(_font(badge_d * 0.32, bold=True))
        painter.drawText(badge.adjusted(0, -badge_d * 0.1, 0, -badge_d * 0.1),
                         int(Qt.AlignmentFlag.AlignCenter), f"{rating}%")
        painter.setFont(_font(badge_d * 0.11, bold=True))
        label = "USERS" if game.user_rating is not None else "CRITICS"
        painter.drawText(QRectF(badge.x(), badge.center().y() + badge_d * 0.12, badge_d, badge_d * 0.2),
                         int(Qt.AlignmentFlag.AlignCenter), label)
        x += badge_d + 3 * unit

    barcode_w = 20 * unit
    barcode = QRectF(width - margin - barcode_w, strip_top + pad, barcode_w, strip_h - 2 * pad)
    _draw_barcode(painter, barcode, game.id)

    credits = []
    if game.developers:
        credits.append(("Developer", ", ".join(game.developers)))
    if game.publishers:
        credits.append(("Publisher", ", ".join(game.publishers)))
    released = _format_release(game.release)
    if released:
        credits.append(("Released", released))
    tail = []
    if game.platforms:
        tail.append(", ".join(game.platforms))
    if game.file_size:
        tail.append(format_size(game.file_size))
    if tail:
        credits.append(("Platform", " · ".join(tail)))

    text_rect = QRectF(x, strip_top + pad * 0.8, barcode.left() - 3 * unit - x, strip_h - 1.6 * pad)
    if credits and text_rect.width() > 0:
        line_h = text_rect.height() / max(4, len(credits))
        key_font = _font(min(2.5 * unit, line_h * 0.62), bold=True)
        value_font = _font(min(2.5 * unit, line_h * 0.62))
        key_w = QFontMetricsF(key_font).horizontalAdvance("DEVELOPER") + 2 * unit
        value_metrics = QFontMetricsF(value_font)
        for index, (key, value) in enumerate(credits):
            row = QRectF(text_rect.x(), text_rect.y() + index * line_h, text_rect.width(), line_h)
            painter.setFont(key_font)
            painter.setPen(QColor(255, 255, 255, 140))
            painter.drawText(row, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), key.upper())
            painter.setFont(value_font)
            painter.setPen(QColor(255, 255, 255, 225))
            value_rect = row.adjusted(key_w, 0, 0, 0)
            painter.drawText(value_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             value_metrics.elidedText(value, Qt.TextElideMode.ElideRight, value_rect.width()))

    painter.end()
    return BackCover(image, rects, description_rect)


def _draw_barcode(painter: QPainter, rect: QRectF, seed: int) -> None:
    """A decorative EAN-style barcode, stable per game."""
    painter.fillRect(rect, QColor(255, 255, 255))
    digits = f"{abs(seed) * 7919 % 10 ** 12:012d}5"
    bars = rect.adjusted(rect.width() * 0.08, rect.height() * 0.1,
                         -rect.width() * 0.08, -rect.height() * 0.3)
    pattern = []
    for digit in digits:
        value = int(digit)
        pattern += [1 + value % 3, 1 + (value * 3) % 2, 1 + (value * 7) % 3, 1]
    total = sum(pattern)
    step = bars.width() / total
    x = bars.x()
    for index, units in enumerate(pattern):
        if index % 2 == 0:
            painter.fillRect(QRectF(x, bars.y(), units * step, bars.height()), QColor(0, 0, 0))
        x += units * step
    painter.setPen(QColor(0, 0, 0))
    painter.setFont(_font(rect.height() * 0.16))
    painter.drawText(QRectF(rect.x(), bars.bottom(), rect.width(), rect.bottom() - bars.bottom()),
                     int(Qt.AlignmentFlag.AlignCenter), " ".join([digits[0], digits[1:7], digits[7:]]))


def render_inner_flap(game: Game, width: int, height: int, base: QColor,
                      hero: QImage | None) -> QImage:
    """The inside of the front cover: key art above a fact sheet."""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    unit = width / 100.0
    margin = 7 * unit
    inner_w = width - 2 * margin
    art_h = height * 0.46
    paper = base.darker(230)
    painter.fillRect(image.rect(), paper)
    if hero is not None and not hero.isNull():
        _draw_cropped(painter, hero, QRectF(0, 0, width, art_h))
    clear = QColor(paper)
    clear.setAlpha(0)
    fade = QLinearGradient(0, art_h * 0.45, 0, art_h)
    fade.setColorAt(0, clear)
    fade.setColorAt(1, paper)
    painter.fillRect(QRectF(0, 0, width, art_h + 1), fade)

    y = art_h - 6 * unit
    # Two lines like the back cover, growing up into the art; shrink only beyond that
    title_font = _font_to_fit(game.title, 6.4 * unit, inner_w, bold=True, lines=2)
    title_rect = QRectF(margin, y - 22 * unit, inner_w, 24 * unit)
    painter.setFont(title_font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
                                     | Qt.TextFlag.TextWordWrap), game.title)
    y += 4 * unit
    painter.fillRect(QRectF(margin, y, 14 * unit, 0.6 * unit), base.lighter(220))
    y += 5 * unit

    facts = []
    ratings = []
    if game.user_rating is not None:
        ratings.append(f"Users {game.user_rating}%")
    if game.critic_rating is not None:
        ratings.append(f"Critics {game.critic_rating}%")
    if ratings:
        facts.append(("Rating", "   ".join(ratings)))
    for key, values in (("Developer", game.developers), ("Publisher", game.publishers),
                        ("Genres", game.genres), ("Themes", game.themes),
                        ("Features", game.features), ("Platforms", game.platforms)):
        if values:
            facts.append((key, ", ".join(values)))
    released = _format_release(game.release)
    if released:
        facts.append(("Released", released))
    if game.file_size:
        facts.append(("Size", format_size(game.file_size)))

    key_font = _font(2.6 * unit, bold=True, letter_spacing=1)
    value_font = _font(3.2 * unit)
    key_h = QFontMetricsF(key_font).height()
    value_metrics = QFontMetricsF(value_font)
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
    bottom = height - 6 * unit
    for key, value in facts:
        value_rect = QRectF(margin, y + key_h + 0.5 * unit, inner_w, value_metrics.height() * 2)
        value = _fit_text(value, value_font, value_rect)
        value_h = value_metrics.boundingRect(value_rect, flags, value).height()
        if y + key_h + value_h > bottom:
            break
        painter.setFont(key_font)
        painter.setPen(QColor(255, 255, 255, 140))
        painter.drawText(QRectF(margin, y, inner_w, key_h), flags, key.upper())
        painter.setFont(value_font)
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(value_rect, flags, value)
        y += key_h + value_h + 3 * unit

    # Fold shadow along the hinge (the right edge once the cover is open)
    hinge = QLinearGradient(width, 0, width - 8 * unit, 0)
    hinge.setColorAt(0, QColor(0, 0, 0, 120))
    hinge.setColorAt(1, QColor(0, 0, 0, 0))
    painter.fillRect(QRectF(width - 8 * unit, 0, 8 * unit, height), hinge)
    painter.end()
    return image


class DescriptionPage:
    """The inside page of the box: the full description, scrollable when long.

    The text is laid out once into a tall strip; :meth:`render` composes the
    visible window of it onto the page for a given scroll offset (in texture
    pixels), so scrolling never re-flows the text.
    """

    def __init__(self, title: str, summary: str | None, width: int, height: int,
                 base: QColor) -> None:
        self.text = _plain_text(summary).replace("\n", "\n\n") or "No description available."
        self.width, self.height, self.base = width, height, base
        unit = width / 100.0
        self.unit = unit
        self.margin = 8 * unit
        self.heading_h = 17 * unit
        self.body = QRectF(self.margin, self.heading_h, width - 2 * self.margin,
                           height - self.heading_h - 7 * unit)
        self.title = title

        flags = int(Qt.TextFlag.TextWordWrap)
        layout_rect = QRectF(0, 0, self.body.width(), 1e7)
        # Largest comfortable size that fits; below the floor, scroll instead
        for size in (4.0, 3.6, 3.3):
            font = _font(size * unit)
            needed = QFontMetricsF(font).boundingRect(layout_rect, flags, self.text).height()
            if needed <= self.body.height():
                break
        self.font = font
        strip_h = int(max(self.body.height(), needed) + 2 * unit)
        self.strip = QImage(int(self.body.width()), strip_h, QImage.Format.Format_ARGB32_Premultiplied)
        self.strip.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.strip)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(238, 238, 242, 235))
        painter.drawText(QRectF(0, 0, self.body.width(), strip_h),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) | flags, self.text)
        painter.end()
        self.max_scroll = max(0.0, needed - self.body.height())

    def render(self, scroll: float) -> QImage:
        width, height, unit, base = self.width, self.height, self.unit, self.base
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        paper = QLinearGradient(0, 0, width, 0)
        paper.setColorAt(0, base.darker(330))
        paper.setColorAt(0.12, base.darker(240))
        paper.setColorAt(1, base.darker(260))
        painter.fillRect(image.rect(), paper)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 0.4 * unit))
        painter.drawRect(QRectF(3 * unit, 3 * unit, width - 6 * unit, height - 6 * unit))

        painter.setFont(_font(2.6 * unit, bold=True, letter_spacing=2))
        painter.setPen(base.lighter(260) if base.lightness() > 20 else QColor(255, 255, 255))
        painter.drawText(QRectF(self.margin, 6 * unit, self.body.width(), 4 * unit),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), "ABOUT THE GAME")
        painter.setFont(_font_to_fit(self.title, 4.6 * unit, self.body.width(), bold=True))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRectF(self.margin, 10 * unit, self.body.width(), 6 * unit),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.title)

        scroll = max(0.0, min(self.max_scroll, scroll))
        painter.drawImage(self.body, self.strip,
                          QRectF(0, scroll, self.body.width(), self.body.height()))

        if self.max_scroll:
            # Fade the cut edges and show where in the text we are
            for top, more in ((True, scroll > 0), (False, scroll < self.max_scroll)):
                if not more:
                    continue
                band = 5 * unit
                y = self.body.top() if top else self.body.bottom() - band
                edge = QLinearGradient(0, y, 0, y + band)
                solid = base.darker(250)
                clear = QColor(solid)
                clear.setAlpha(0)
                edge.setColorAt(0, solid if top else clear)
                edge.setColorAt(1, clear if top else solid)
                painter.fillRect(QRectF(0, y, width, band), edge)
            track = QRectF(width - 5 * unit, self.body.top(), 0.8 * unit, self.body.height())
            painter.fillRect(track, QColor(255, 255, 255, 30))
            visible = self.body.height() / (self.body.height() + self.max_scroll)
            thumb_h = max(4 * unit, track.height() * visible)
            thumb_y = track.top() + (track.height() - thumb_h) * scroll / self.max_scroll
            painter.fillRect(QRectF(track.left(), thumb_y, track.width(), thumb_h),
                             QColor(255, 255, 255, 150))
            if scroll < self.max_scroll:
                painter.setFont(_font(2.3 * unit, bold=True, letter_spacing=1))
                painter.setPen(QColor(255, 255, 255, 150))
                painter.drawText(QRectF(self.margin, self.body.bottom() + 1.5 * unit,
                                        self.body.width(), 4 * unit),
                                 int(Qt.AlignmentFlag.AlignCenter), "SCROLL FOR MORE ▾")

        # Fold shadow along the hinge on the left
        hinge = QLinearGradient(0, 0, 8 * unit, 0)
        hinge.setColorAt(0, QColor(0, 0, 0, 140))
        hinge.setColorAt(1, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0, 0, 8 * unit, height), hinge)
        painter.end()
        return image


# ------------------------------------------------------------------
# The 3D view
# ------------------------------------------------------------------

def _rotate(point: tuple[float, float, float], yaw: float, pitch: float) -> tuple[float, float, float]:
    """Rotate about Y (yaw) then X (pitch); angles in radians."""
    x, y, z = point
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    x, z = x * cos_y + z * sin_y, -x * sin_y + z * cos_y
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    y, z = y * cos_p - z * sin_p, y * sin_p + z * cos_p
    return x, y, z


class GameBoxView(QWidget):
    """Interactive 3D big box for one game."""

    # True while the back of the box faces the viewer (after a flip settles)
    side_changed = pyqtSignal(bool)
    # True when the front cover starts opening, False when it starts closing
    cover_changed = pyqtSignal(bool)
    # Index into the screenshots passed to :meth:`set_back`
    screenshot_clicked = pyqtSignal(int)
    # The download animation ended (played out or skipped)
    disc_finished = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(260, 320)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # Hover feedback over the clickable parts of the box
        self.setMouseTracking(True)

        self.yaw = BOX_REST_YAW
        self.pitch = BOX_REST_PITCH
        self.open = 0.0  # how far the front cover is swung open, 0..1
        self.aspect = BOX_DEFAULT_ASPECT  # height / width of the front face

        # Full-resolution textures and their copies scaled to the current size
        self._textures: dict[str, QImage] = {}
        self._scaled: dict[str, QImage] = {}
        self._scaled_height = 0
        self._side_color = QColor(40, 44, 52)
        self._screenshot_rects: list[QRectF] = []
        self._description_rect: QRectF | None = None
        self._page: DescriptionPage | None = None
        self._page_scroll = 0.0
        self._showing_back = False
        self._cover_open = False

        self._press_pos: QPointF | None = None
        self._last_pos = QPointF()
        self._dragged = False

        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_step)
        self._animation.finished.connect(self._emit_side)
        self._animation.finished.connect(self._run_next_animation)
        self._from = (self.yaw, self.pitch, self.open)
        self._to = self._from
        # Second leg of a two-step move, started when the current one finishes
        self._next_animation: Callable[[], None] | None = None

        # Download animation: progress 0..1 while it plays, None otherwise
        self._disc: DiscSequence | None = None
        self._disc_t: float | None = None
        self._disc_was_open = False
        self._disc_animation = QVariantAnimation(self)
        self._disc_animation.setStartValue(0.0)
        self._disc_animation.setEndValue(1.0)
        self._disc_animation.setDuration(BOX_DISC_MS)
        self._disc_animation.valueChanged.connect(self._on_disc_step)
        self._disc_animation.finished.connect(self._finish_disc)
        self._disc_delay = QTimer(self)
        self._disc_delay.setSingleShot(True)
        self._disc_delay.timeout.connect(self._disc_animation.start)

    # -- Content ------------------------------------------------------------

    def set_front(self, image: QImage, side_color: QColor) -> None:
        """Use *image* as the cover; its aspect ratio decides the box shape."""
        if not image.isNull() and image.width():
            # Clamp so odd artwork (banners, squares) still makes a box shape
            self.aspect = min(1.6, max(1.1, image.height() / image.width()))
        self._side_color = side_color
        cap = render_cap(BOX_BACK_TEXTURE_WIDTH, max(8, int(BOX_BACK_TEXTURE_WIDTH * BOX_DEPTH_RATIO)),
                         side_color)
        self._set_texture("top", cap)
        self._set_texture("bottom", cap)
        self._set_texture("front", image)

    def set_spine(self, image: QImage) -> None:
        self._set_texture("spine", image)

    def set_back(self, back: BackCover) -> None:
        self._screenshot_rects = back.screenshot_rects
        self._description_rect = back.description_rect
        self._set_texture("back", back.image)

    def set_inside(self, flap: QImage, page: DescriptionPage) -> None:
        """The inside of the cover and the description page it reveals."""
        if self._page is None or self._page.text != page.text:
            self._page_scroll = 0.0
        self._page = page
        self._set_texture("inside", flap)
        self._render_page()

    def set_disc(self, image: QImage) -> None:
        """The disc that comes out of the box when a download starts."""
        self._disc = DiscSequence(image)

    def back_texture_size(self) -> tuple[int, int]:
        """Pixel size a back-cover (or inside) texture should be rendered at."""
        return BOX_BACK_TEXTURE_WIDTH, int(BOX_BACK_TEXTURE_WIDTH * self.aspect)

    def spine_texture_size(self) -> tuple[int, int]:
        width, height = self.back_texture_size()
        return max(8, int(width * BOX_DEPTH_RATIO)), height

    def _set_texture(self, name: str, image: QImage) -> None:
        self._textures[name] = image
        self._scaled.pop(name, None)
        self.update()

    def is_showing_back(self) -> bool:
        return math.cos(math.radians(self.yaw)) < 0

    def is_cover_open(self) -> bool:
        return self._cover_open

    def has_more_description(self) -> bool:
        """Whether the back had to cut the description short."""
        return self._description_rect is not None

    # -- Description page -----------------------------------------------------

    def _render_page(self) -> None:
        if self._page is not None:
            self._set_texture("page", self._page.render(self._page_scroll))

    def scroll_page(self, screen_dy: float) -> bool:
        """Scroll the open description page by *screen_dy* on-screen pixels."""
        if not self._cover_open or self._page is None or not self._page.max_scroll:
            return False
        camera, focal, _, _ = self._camera()
        on_screen = max(1.0, focal * self.aspect / camera)
        before = self._page_scroll
        self._page_scroll = max(0.0, min(self._page.max_scroll,
                                         before + screen_dy * self._page.height / on_screen))
        if self._page_scroll != before:
            self._render_page()
        return True

    def gamepad_scroll(self, _dx: float, dy: float) -> bool:
        """Right stick: scroll the open page (the navigator's scroll hook)."""
        return self.scroll_page(dy)

    def gamepad_page_scroll(self, direction: int) -> bool:
        """Triggers: scroll the open page by most of its height."""
        return self.scroll_page(direction * self.height() * 0.45)

    # -- Motion -------------------------------------------------------------

    # -- Download animation ---------------------------------------------------

    def insert_disc(self) -> None:
        """Open the box and put its disc into a drive (a download started)."""
        if self._disc is None or self.is_inserting_disc():
            return
        self._disc_was_open = self._cover_open
        turning = self.open_cover()
        self._disc_t = 0.0
        # The disc follows the cover opening, so wait while the box turns round first
        self._disc_delay.start(BOX_FLIP_MS if turning else 0)

    def is_inserting_disc(self) -> bool:
        return self._disc_t is not None

    def skip_disc(self) -> None:
        """Jump to the end of the download animation."""
        if self.is_inserting_disc():
            self._disc_delay.stop()
            self._disc_animation.stop()
            self._finish_disc()

    def _on_disc_step(self, value: float) -> None:
        self._disc_t = value
        self.update()

    def _finish_disc(self) -> None:
        self._disc_t = None
        if not self._disc_was_open:
            self.close_cover()
        self.update()
        self.disc_finished.emit()

    def reset_pose(self, intro: bool = True) -> None:
        """Face the closed front again; *intro* swings the box in from an angle."""
        self._disc_delay.stop()
        self._disc_animation.stop()
        self._disc_t = None
        self._animation.stop()
        self._set_cover_open(False)
        self.open = 0.0
        rest = BOX_REST_YAW
        if intro:
            self.yaw, self.pitch = rest - 70.0, BOX_REST_PITCH + 6.0
            self._animate_to(rest, BOX_REST_PITCH, 0.0, BOX_FLIP_MS + 300)
        else:
            self.yaw, self.pitch = rest, BOX_REST_PITCH
            self._from = self._to = (self.yaw, self.pitch, self.open)
            self.update()
            self._emit_side()

    def _target_yaw(self) -> float:
        running = self._animation.state() == QVariantAnimation.State.Running
        return self._to[0] if running else self.yaw

    def flip(self, direction: int = 1) -> None:
        """Turn the box over to its other side (closing the cover first)."""
        target = self._nearest_rest(self._target_yaw())
        self._set_cover_open(False)
        self._animate_to(target + 180.0 * direction, BOX_REST_PITCH, 0.0, BOX_FLIP_MS)

    def open_cover(self) -> bool:
        """Swing the front cover open like a book, turning to the front if needed.

        Returns:
            True when the box first turns round, delaying the opening by BOX_FLIP_MS.
        """
        yaw = self._target_yaw()
        front = BOX_REST_YAW + round((yaw - BOX_REST_YAW) / 360.0) * 360.0
        target = BOX_OPEN_YAW + round((front - BOX_OPEN_YAW) / 360.0) * 360.0
        self._set_cover_open(True)
        if self.open < 0.01 and abs(yaw - front) > BOX_OPEN_TURN_FIRST:
            # Turn the closed box round first: a cover swinging open while the
            # box spins shows it sticking out of the back and sides
            self._animate_to(front, BOX_REST_PITCH, 0.0, BOX_FLIP_MS,
                             then=lambda: self._animate_to(target, BOX_OPEN_PITCH, 1.0, BOX_OPEN_MS))
            return True
        self._animate_to(target, BOX_OPEN_PITCH, 1.0, BOX_OPEN_MS)
        return False

    def close_cover(self) -> None:
        self._set_cover_open(False)
        self._animate_to(self._nearest_rest(self._target_yaw()), BOX_REST_PITCH, 0.0, BOX_OPEN_MS)

    def toggle_cover(self) -> None:
        if self._cover_open:
            self.close_cover()
        else:
            self.open_cover()

    def _set_cover_open(self, opened: bool) -> None:
        if opened != self._cover_open:
            self._cover_open = opened
            self.cover_changed.emit(opened)

    def _nearest_rest(self, yaw: float) -> float:
        """Closest resting yaw: the front at BOX_REST_YAW, the back 180° on."""
        return BOX_REST_YAW + round((yaw - BOX_REST_YAW) / 180.0) * 180.0

    def _animate_to(self, yaw: float, pitch: float, opened: float, duration: int,
                    then: Callable[[], None] | None = None) -> None:
        """Animate to a pose; *then* runs once it is reached (a new move cancels it)."""
        self._animation.stop()
        self._next_animation = then
        self._from = (self.yaw, self.pitch, self.open)
        self._to = (yaw, pitch, opened)
        self._animation.setDuration(duration)
        self._animation.start()

    def _run_next_animation(self) -> None:
        step, self._next_animation = self._next_animation, None
        if step is not None:
            step()

    def _on_animation_step(self, value: float) -> None:
        self.yaw, self.pitch, self.open = (
            start + (end - start) * value for start, end in zip(self._from, self._to))
        self.update()

    def _emit_side(self) -> None:
        back = self.is_showing_back()
        if back != self._showing_back:
            self._showing_back = back
            self.side_changed.emit(back)

    # -- Input --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self.is_inserting_disc():
            self.skip_disc()  # any click skips the download animation
            return
        self._animation.stop()
        self._press_pos = event.position()
        self._last_pos = event.position()
        self._dragged = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._press_pos is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._clickable_at(event.position())
                           else Qt.CursorShape.OpenHandCursor)
            return
        delta = event.position() - self._last_pos
        self._last_pos = event.position()
        if (event.position() - self._press_pos).manhattanLength() > _CLICK_SLOP:
            self._dragged = True
        if self._dragged:
            self.yaw += delta.x() * _YAW_PER_PX
            self.pitch = max(-_PITCH_LIMIT, min(_PITCH_LIMIT, self.pitch - delta.y() * _PITCH_PER_PX))
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._press_pos is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._press_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self._dragged:
            # Settle on whichever side faces the viewer most (or the open book)
            if self._cover_open:
                self.open_cover()
            else:
                self._animate_to(self._nearest_rest(self.yaw), BOX_REST_PITCH, 0.0,
                                 BOX_FLIP_MS // 2 + 150)
            return
        self.click_at(event.position())

    def click_at(self, pos: QPointF) -> None:
        """React to a click on whatever part of the box is under *pos*."""
        face = self._face_at(pos)
        if self._cover_open:
            if face != "page":
                self.close_cover()
            return
        index = self.screenshot_at(pos)
        if index is not None:
            self.screenshot_clicked.emit(index)
        elif self.description_at(pos) or face == "front":
            self.open_cover()
        elif face is not None:
            self.flip()

    def _clickable_at(self, pos: QPointF) -> bool:
        if self._cover_open:
            return self._face_at(pos) not in ("page", None)
        return (self._face_at(pos) == "front" or self.screenshot_at(pos) is not None
                or self.description_at(pos))

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if not self.scroll_page(-event.angleDelta().y() / 120 * 48):
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if self.is_inserting_disc() and key in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                                Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            self.skip_disc()
            return
        steps = {Qt.Key.Key_Down: 48, Qt.Key.Key_Up: -48,
                 Qt.Key.Key_PageDown: self.height() * 0.45, Qt.Key.Key_PageUp: -self.height() * 0.45}
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._cover_open or not self.is_showing_back():
                self.toggle_cover()
            else:
                self.flip()
        elif key == Qt.Key.Key_Escape and self._cover_open:
            self.close_cover()
        elif key in steps and self._cover_open:
            self.scroll_page(steps[key])
        elif key == Qt.Key.Key_Right:
            self.flip(1)
        elif key == Qt.Key.Key_Left:
            self.flip(-1)
        else:
            super().keyPressEvent(event)

    # -- Geometry -----------------------------------------------------------

    def _dimensions(self) -> tuple[float, float, float]:
        """Half extents (x, y, z) of the box in model units (front width = 1)."""
        return 0.5, self.aspect / 2, BOX_DEPTH_RATIO / 2

    def _hinge(self) -> tuple[float, float]:
        """(cover angle in radians, sideways shift that centres the open book)."""
        theta = math.radians(self.open * BOX_OPEN_ANGLE)
        half_w = self._dimensions()[0]
        return theta, half_w * (1 - math.cos(theta)) / 2

    def _camera(self) -> tuple[float, float, float, float]:
        """Return (camera distance, focal length, centre x, centre y)."""
        half_w, half_h, half_d = self._dimensions()
        camera = 5.0 * half_h * 2
        # Size from the largest extent so the box doesn't zoom while turning;
        # an open book (centred by the hinge shift) is up to twice as wide
        _, shift = self._hinge()
        radius_xz = math.hypot(half_w + shift, half_d)
        near = camera / (camera - radius_xz)
        fit_h = self.height() * 0.80 / (2 * half_h * near)
        fit_w = self.width() * 0.86 / (2 * radius_xz * near)
        # Pull back and up while the download animation needs room for the drive
        stage = DiscSequence.stage(self._disc_t) if self._disc_t is not None else 0.0
        focal = camera * min(fit_h, fit_w) * (1 - 0.32 * stage)
        return camera, focal, self.width() / 2, self.height() * (0.47 - 0.14 * stage)

    def _project(self, point: tuple[float, float, float]) -> QPointF:
        camera, focal, cx, cy = self._camera()
        x, y, z = point
        scale = focal / (camera - z)
        return QPointF(cx + x * scale, cy - y * scale)

    def _scene(self) -> list[tuple[str, QPolygonF, float]]:
        """Visible faces in paint order as (texture name, projected quad, brightness).

        Each face's corners are TL/TR/BR/BL as seen from outside. The box
        body is convex, so culling suffices for it; the cover flap always lies
        in front of the front plane, so it is painted after the body when the
        viewer is in front of that plane and before it otherwise.
        """
        w, h, d = self._dimensions()
        theta, shift = self._hinge()
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        camera = self._camera()[0]

        def place(name, corners, normal):
            rotated = [_rotate((x + shift, y, z), yaw, pitch) for x, y, z in corners]
            n = _rotate(normal, yaw, pitch)
            centre = [sum(c[i] for c in rotated) / 4 for i in range(3)]
            to_camera = (-centre[0], -centre[1], camera - centre[2])
            if sum(n[i] * to_camera[i] for i in range(3)) <= 1e-6:
                return None
            light = max(0.0, sum(n[i] * _LIGHT[i] for i in range(3)))
            # Plenty of ambient light, like a softbox photo: faces differ, none goes murky
            return name, QPolygonF([self._project(c) for c in rotated]), 0.64 + 0.36 * light

        body = [
            # Under a closed cover the page is hidden; skipping it avoids seams
            ("page", [(-w, h, d), (w, h, d), (w, -h, d), (-w, -h, d)], (0, 0, 1)) if self.open > 0.001 else None,
            ("back", [(w, h, -d), (-w, h, -d), (-w, -h, -d), (w, -h, -d)], (0, 0, -1)),
            ("spine", [(-w, h, -d), (-w, h, d), (-w, -h, d), (-w, -h, -d)], (-1, 0, 0)),
            ("spine", [(w, h, d), (w, h, -d), (w, -h, -d), (w, -h, d)], (1, 0, 0)),
            ("top", [(-w, h, -d), (w, h, -d), (w, h, d), (-w, h, d)], (0, 1, 0)),
            ("bottom", [(-w, -h, d), (w, -h, d), (w, -h, -d), (-w, -h, -d)], (0, -1, 0)),
        ]
        body_faces = [face for face in (place(*f) for f in body if f is not None) if face]

        # The cover swings about the hinge where it meets the left spine
        width = 2 * w
        lift = 0.002  # keep the closed cover just proud of the page
        outer_x, outer_z = -w + width * math.cos(theta), d + lift + width * math.sin(theta)
        hinge_top, hinge_bottom = (-w, h, d + lift), (-w, -h, d + lift)
        outer_top, outer_bottom = (outer_x, h, outer_z), (outer_x, -h, outer_z)
        normal = (-math.sin(theta), 0.0, math.cos(theta))
        flap = [face for face in (
            place("front", [hinge_top, outer_top, outer_bottom, hinge_bottom], normal),
            place("inside", [outer_top, hinge_top, hinge_bottom, outer_bottom],
                  tuple(-c for c in normal)),
        ) if face]

        front_n = _rotate((0, 0, 1), yaw, pitch)
        front_c = _rotate((shift, 0, d), yaw, pitch)
        in_front = (front_n[0] * -front_c[0] + front_n[1] * -front_c[1]
                    + front_n[2] * (camera - front_c[2])) > 0
        return body_faces + flap if in_front else flap + body_faces

    def _face_transform(self, size: tuple[float, float], quad: QPolygonF) -> QTransform | None:
        width, height = size
        source = QPolygonF([QPointF(0, 0), QPointF(width, 0), QPointF(width, height), QPointF(0, height)])
        transform = QTransform()
        if not QTransform.quadToQuad(source, quad, transform):
            return None
        return transform

    def _face_at(self, pos: QPointF) -> str | None:
        """Name of the topmost face under *pos*."""
        for name, quad, _ in reversed(self._scene()):
            if quad.containsPoint(pos, Qt.FillRule.OddEvenFill):
                return name
        return None

    def _back_point(self, pos: QPointF) -> QPointF | None:
        """Map widget *pos* onto the back cover (0..1), if the back is on top there."""
        for name, quad, _ in reversed(self._scene()):
            if not quad.containsPoint(pos, Qt.FillRule.OddEvenFill):
                continue
            if name != "back":
                return None
            transform = self._face_transform((1.0, 1.0), quad)
            if transform is None:
                return None
            inverted, ok = transform.inverted()
            return inverted.map(pos) if ok else None
        return None

    def screenshot_at(self, pos: QPointF) -> int | None:
        """Index of the back-cover screenshot under *pos*, if the back is visible."""
        local = self._back_point(pos)
        if local is None:
            return None
        for index, rect in enumerate(self._screenshot_rects):
            if rect.contains(local):
                return index
        return None

    def description_at(self, pos: QPointF) -> bool:
        """Whether *pos* is on a cut-short blurb on the visible back."""
        if self._description_rect is None:
            return False
        local = self._back_point(pos)
        return local is not None and self._description_rect.contains(local)

    # -- Painting -----------------------------------------------------------

    def _texture(self, name: str, face_height: float) -> QImage | None:
        """The texture for *name*, downscaled once to roughly its on-screen size."""
        source = self._textures.get(name)
        if source is None or source.isNull():
            return None
        target = int(face_height * self.devicePixelRatioF() * 1.25)
        if abs(target - self._scaled_height) > max(24, self._scaled_height * 0.15):
            self._scaled.clear()
            self._scaled_height = target
        scaled = self._scaled.get(name)
        if scaled is None:
            scaled = source
            if source.height() > self._scaled_height > 0:
                scaled = source.scaledToHeight(self._scaled_height, Qt.TransformationMode.SmoothTransformation)
            # The page is re-rendered on every scroll step and must stay crisp
            if name != "page":
                scaled = _finish_texture(scaled, name)
            self._scaled[name] = scaled
        return scaled

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self._paint_backdrop(painter)
        faces = self._scene()

        camera, focal, _, _ = self._camera()
        face_height = focal * self.aspect / camera
        self._paint_shadow(painter)
        for name, quad, brightness in faces:
            self._paint_face(painter, name, quad, brightness, face_height)

        if self._disc is not None and self._disc_t is not None:
            page = next((quad for name, quad, _ in faces if name == "page"), None)
            self._disc.paint(painter, self._disc_t, self.width(), self.height(), page,
                             self.palette().color(QPalette.ColorRole.WindowText))
        painter.end()

    def _paint_backdrop(self, painter: QPainter) -> None:
        window = self.palette().color(self.backgroundRole())
        dark = window.lightness() < 128
        # Reach the farthest corner, and fall short of the side edges' middles,
        # so no edge of the widget shows against the window
        radius = min(self.width() / 2, math.hypot(self.width() / 2, self.height() * 0.58))
        glow = QRadialGradient(QPointF(self.width() / 2, self.height() * 0.42), radius)
        # A spotlight on the box that fades out into the window colour
        glow.setColorAt(0, window.lighter(150 if dark else 106))
        glow.setColorAt(1, window)
        painter.fillRect(self.rect(), glow)

    def _footprints(self) -> list[tuple[tuple[float, float], tuple[float, float], float, float]]:
        """What touches the floor, as bands (start, end, half width, shadow strength).

        Points are (x, z) in model space. The box's base is one wide band; a
        swung-open cover stands on its bottom edge, so it only adds a thin one.
        """
        w, _, d = self._dimensions()
        bands = [((-w, 0.0), (w, 0.0), d, 1.0)]
        if self.open > 0.001:
            theta, _ = self._hinge()
            outer = (-w + 2 * w * math.cos(theta), d + 2 * w * math.sin(theta))
            bands.append(((-w, d), outer, 0.004, 0.6))
        return bands

    def _floor_polygon(self, start: tuple[float, float], end: tuple[float, float],
                       half_width: float, pad: float,
                       offset: tuple[float, float] = (0.0, 0.0)) -> QPolygonF:
        """Project a floor band, grown on every side by *pad* model units."""
        _, h, _ = self._dimensions()
        _, shift = self._hinge()
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        dx, dz = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dz) or 1.0
        ux, uz = dx / length, dz / length  # along the band
        nx, nz = -uz, ux  # across it
        across = half_width + pad
        corners = []
        for (x, z), along in ((start, -pad), (end, pad)):
            for side in ((-1, 1) if along < 0 else (1, -1)):
                corners.append((x + ux * along + nx * across * side + offset[0],
                                z + uz * along + nz * across * side + offset[1]))
        return QPolygonF([self._project(_rotate((x + shift, -h, z), yaw, pitch)) for x, z in corners])

    def _paint_shadow(self, painter: QPainter) -> None:
        """A dark contact line where the box meets the floor, softening outwards.

        Stacked translucent rings stand in for a blur. The soft part leans
        away from the light (upper left, front) like a real cast shadow.
        """
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        rings = 9
        for start, end, half_width, strength in self._footprints():
            for ring in range(rings, 0, -1):
                t = ring / rings
                pad = 0.004 + 0.16 * t * t
                painter.setBrush(QColor(0, 0, 0, int((12 + 10 * (1 - t)) * strength)))
                painter.drawPolygon(self._floor_polygon(start, end, half_width, pad,
                                                        (0.05 * t, -0.04 * t)))
            painter.setBrush(QColor(0, 0, 0, int(150 * strength)))
            painter.drawPolygon(self._floor_polygon(start, end, half_width, 0.004))
        painter.restore()

    def _paint_face(self, painter: QPainter, name: str, quad: QPolygonF,
                    brightness: float, face_height: float) -> None:
        texture = self._texture(name, face_height)
        if texture is None:
            colour = QColor(self._side_color)
            if name not in ("spine", "top", "bottom"):
                colour = colour.lighter(120)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour.darker(int(100 / max(brightness, 0.2))))
            painter.drawPolygon(quad)
        else:
            transform = self._face_transform((texture.width(), texture.height()), quad)
            if transform is None:
                return
            painter.save()
            painter.setTransform(transform)
            painter.drawImage(0, 0, texture)
            rect = QRectF(0, 0, texture.width(), texture.height())
            # Pages are read face-on: light them gently so text stays crisp
            shade = 140 if name in ("page", "inside") else 210
            painter.fillRect(rect, QColor(0, 0, 0, int((1 - brightness) * shade)))
            if name in ("front", "back"):
                self._paint_gloss(painter, rect, name)
            painter.restore()

        # Just enough of an edge to hide the seams between faces; the rounded
        # cardboard look itself is baked into the textures
        painter.setPen(QPen(QColor(255, 255, 255, int(8 + 22 * brightness)), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(quad)

    def _paint_gloss(self, painter: QPainter, rect: QRectF, name: str) -> None:
        """A shrink-wrap sheen that slides across the face as the box turns."""
        angle = math.radians(self.yaw - BOX_REST_YAW + (180 if name == "back" else 0)
                             - self.open * BOX_OPEN_ANGLE)
        # At rest the streak sits in the upper left, where the light comes from,
        # so it catches the eye without fogging the artwork
        offset = 0.22 + math.sin(angle) * 1.4
        if not -0.4 < offset < 1.4:
            return  # band is off the face (a stop-less gradient paints black-white)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        steps = 48
        for step in range(steps + 1):
            stop = step / steps
            distance = abs(stop - offset)
            # A crisp streak, as off stretched plastic, on a faint wider sheen
            alpha = 38 * max(0.0, 1 - distance / 0.06) + 14 * max(0.0, 1 - distance / 0.3)
            gradient.setColorAt(stop, QColor(255, 255, 255, int(alpha)))
        painter.fillRect(rect, gradient)
