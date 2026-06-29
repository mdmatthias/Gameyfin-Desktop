"""Animated loading overlay shown while web pages load."""

import math

from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QFont,
    QFontMetrics,
    QIcon,
    QPixmap,
)
from PyQt6.QtCore import Qt, QTimer, QRectF


class LoadingOverlay(QWidget):
    """A full-size overlay with an animated spinner and logo, shown during page loads.

    Automatically hides once ``hide()`` is called — uses a smooth fade-out
    transition over 400 ms instead of an abrupt disappearance.
    """

    def __init__(self, parent: QWidget | None = None, app_icon: QIcon | None = None) -> None:
        super().__init__(parent)
        self._app_icon = app_icon
        self._base_icon_pixmap: QPixmap | None = None  # unscaled original
        self._opacity = 1.0
        self._fading_out = False

        # Timer for animation loop (~30 fps)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)  # ~30fps
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

        # Hide initially; show() will trigger the fade-in
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_app_icon(self, icon: QIcon) -> None:
        """Set or update the app icon used in the center of the spinner."""
        if icon != self._app_icon:
            self._app_icon = icon
            self._base_icon_pixmap = None
            self.update()

    def _load_base_icon(self) -> QPixmap | None:
        """Load the raw icon pixmap once (no scaling)."""
        if self._app_icon is None or self._base_icon_pixmap is not None:
            return self._base_icon_pixmap
        pixmap = self._app_icon.pixmap(512, 512)  # request high-res source
        if pixmap.isNull():
            return None
        # Neutralize the device-pixel ratio. On HiDPI displays QIcon.pixmap()
        # returns a pixmap with devicePixelRatio > 1, which scaled() preserves;
        # the layout math below uses pixmap.width() (device pixels) while
        # drawPixmap() renders at the logical (smaller) size, which pushed the
        # logo into the top-left quadrant of the glow circle. Working in plain
        # pixels keeps the logo centered.
        pixmap.setDevicePixelRatio(1.0)
        self._base_icon_pixmap = pixmap
        return pixmap

    def show_overlay(self) -> None:
        """Show the loading overlay (fade-in from transparent)."""
        self._opacity = 0.0
        self._fading_out = False
        self.show()
        self.raise_()
        self.update()

    def hide_overlay(self) -> None:
        """Hide the loading overlay (smooth fade-out over 400 ms).

        Uses ``isHidden()`` (true only when ``hide()`` was explicitly called)
        rather than ``isVisible()``/opacity so the fade-out is triggered
        reliably even if the page finishes loading before the fade-in starts
        or while the window is minimized — otherwise the overlay could get
        stuck fully opaque over an already-loaded page.
        """
        if self.isHidden():
            return
        self._fading_out = True
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Apply global opacity
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, self._opacity)))

        # Background — dark tinted overlay
        bg_color = QColor(0, 0, 0, int(220 * max(0.0, min(1.0, self._opacity))))
        painter.fillRect(self.rect(), bg_color)

        # Center point
        cx = self.width() / 2
        cy = self.height() / 2

        # --- App logo (pulsing) ---
        self._draw_logo(painter, cx, cy)

        # --- "Loading..." text ---
        self._draw_loading_text(painter, cx, cy)

        painter.restore()
        painter.end()

    def _draw_logo(self, painter: QPainter, cx: float, cy: float) -> None:
        """Draw the app icon as a pulsing center element."""
        if self._app_icon is None:
            return

        # Load high-res source once
        base = self._load_base_icon()
        if base is None or base.isNull():
            return

        # Logo size relative to window, with breathing animation
        base_size = min(self.width(), self.height()) * 0.06
        base_size = max(base_size, 28)  # minimum 28px

        # Pulse effect: scale oscillates between ~0.92 and ~1.08
        pulse_scale = 1.0 + 0.08 * math.sin(_pulse_timer() * 2.5)
        logo_size = int(base_size * pulse_scale)

        # Scale fresh each frame for smooth animation
        pixmap = base.scaled(
            logo_size, logo_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if pixmap.isNull():
            return

        # Draw with slight glow behind it
        glow_color = QColor("#FFFFFF")
        glow_color.setAlpha(int(30 * self._opacity))
        painter.setPen(glow_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glow_margin = 6
        painter.drawEllipse(
            QRectF(
                cx - (pixmap.width() // 2 + glow_margin),
                cy - (pixmap.height() // 2 + glow_margin),
                pixmap.width() + glow_margin * 2,
                pixmap.height() + glow_margin * 2,
            )
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPixmap(
            int(cx - pixmap.width() / 2),
            int(cy - pixmap.height() / 2),
            pixmap,
        )

    def _draw_loading_text(self, painter: QPainter, cx: float, cy: float) -> None:
        """Draw pulsing 'Loading...' text below the spinner."""
        font = QFont("Sans Serif", 12)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)

        text = "Loading..."
        fm = QFontMetrics(font)
        bounding_rect = fm.boundingRect(text)
        text_x = int(cx - bounding_rect.width() / 2)
        text_y = int(cy + min(self.width(), self.height()) * 0.08 + 24)

        # Pulse effect — subtle opacity oscillation
        pulse = 0.5 + 0.5 * math.sin(_text_pulse_timer() * 3.0)
        alpha = int((0.4 + 0.6 * pulse) * self._opacity * 255)
        text_color = QColor(236, 240, 241)
        text_color.setAlpha(alpha)

        painter.setPen(text_color)
        painter.drawText(text_x, text_y, text)

    # ------------------------------------------------------------------
    # Animation tick
    # ------------------------------------------------------------------

    def _on_tick(self) -> None:
        if self._fading_out:
            self._opacity -= 0.03  # ~400ms fade-out at 30fps
            if self._opacity <= 0.0:
                self._opacity = 0.0
                self.hide()
                return
        else:
            # Fade in on first show
            if self._opacity < 1.0:
                self._opacity += 0.05
                if self._opacity > 1.0:
                    self._opacity = 1.0

        self.update()


# Global timers for smooth animations (shared across all overlay instances)
def _pulse_timer() -> float:
    """Return a monotonically increasing float for logo pulse animation."""
    import time
    return time.monotonic()


def _text_pulse_timer() -> float:
    """Return a monotonically increasing float for text pulse animation."""
    import time
    return time.monotonic()
