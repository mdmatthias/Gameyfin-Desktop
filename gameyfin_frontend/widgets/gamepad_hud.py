"""On-screen gamepad affordances: the hint bar and the full help overlay.

Both are purely informational — they never take focus, so they cannot interfere
with gamepad navigation.
"""

from __future__ import annotations

from typing import Any, Iterable

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..utils import (accent_color, contrasting_text_color, muted_text_color,
                     surface_color)

# Full binding reference, also used by the help overlay.
BINDINGS: tuple[tuple[str, str], ...] = (
    ("D-pad / Left stick", "Move between items"),
    ("A", "Select / activate"),
    ("B", "Back, cancel or close"),
    ("Y", "Refresh / reload"),
    ("LB / RB", "Previous / next tab"),
    ("LT / RT", "Page up / page down"),
    ("Right stick", "Scroll"),
    ("Start", "Show this help"),
)


def _badge_style(widget: QWidget | None) -> str:
    """Style a button badge in the theme's accent colour."""
    accent = accent_color(widget)
    return (
        f"background-color: {accent.name()};"
        f"color: {contrasting_text_color(accent).name()};"
        "border-radius: 8px;"
        "padding: 1px 7px;"
        "font-weight: bold;"
    )


def _make_badge(text: str, parent: QWidget | None = None) -> QLabel:
    badge = QLabel(text, parent)
    badge.setStyleSheet(_badge_style(parent))
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return badge


class GamepadHintBar(QWidget):
    """A slim strip of ``[button] action`` chips shown while a pad is connected.

    The strip never dictates a minimum window width. As it narrows, the action
    texts shrink first (the button badges and the controller name always stay
    readable); only when even the bare badges no longer fit do chips drop off
    the right end.
    """

    _CHIP_SPACING = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 6, 12, 6)
        self._layout.setSpacing(8)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {muted_text_color(self)};")
        self._layout.addWidget(self._status)
        self._layout.addStretch(1)
        self._status_text = ""
        self._chips: list[tuple[QWidget, QLabel, QLabel, str]] = []
        self._relaying_out = False
        self.set_hints([])

    def minimumSizeHint(self) -> QSize:  # noqa: D102 - Qt override
        return QSize(0, super().minimumSizeHint().height())

    def refresh_theme_colors(self) -> None:
        """Re-apply palette-derived colours after the theme changed."""
        self._status.setStyleSheet(f"color: {muted_text_color(self)};")
        style = _badge_style(self)
        for _chip, badge, _label, _action in self._chips:
            badge.setStyleSheet(style)

    def set_status(self, text: str) -> None:
        """Set the left-hand status text (device name, mouse mode, …)."""
        self._status_text = text
        self._relayout()

    def set_hints(self, hints: Iterable[tuple[str, str]]) -> None:
        """Replace the chips with ``(button, action)`` pairs."""
        for chip, _badge, _label, _action in self._chips:
            self._layout.removeWidget(chip)
            chip.deleteLater()
        self._chips = []

        for button, action in hints:
            chip = QWidget(self)
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(0, 0, 0, 0)
            chip_layout.setSpacing(self._CHIP_SPACING)
            badge = _make_badge(button, chip)
            label = QLabel(action)
            label.setStyleSheet("color: palette(text);")
            chip_layout.addWidget(badge)
            chip_layout.addWidget(label)
            self._layout.addWidget(chip)
            self._chips.append((chip, badge, label, action))
        self._relayout()

    def resizeEvent(self, event: Any) -> None:  # noqa: ANN401, D102 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        """Fit the strip into the current width by scaling its texts down."""
        if self._relaying_out:
            return
        self._relaying_out = True
        try:
            self._fit_contents()
        finally:
            self._relaying_out = False

    def _fit_contents(self) -> None:
        margins = self._layout.contentsMargins()
        available = max(0, self.width() - margins.left() - margins.right())
        spacing = self._layout.spacing()
        status_metrics = QFontMetrics(self._status.font())

        # The controller name comes first, but never eats more than half the bar.
        status_full = status_metrics.horizontalAdvance(self._status_text)
        status_width = min(status_full, available // 2 if self._chips else available)
        self._status.setText(
            self._status_text
            if status_width >= status_full
            else status_metrics.elidedText(
                self._status_text, Qt.TextElideMode.ElideRight, status_width
            )
        )
        available -= status_width + (spacing if self._status_text else 0)

        # Badges are the next priority: shed chips from the right until they fit.
        visible = list(self._chips)
        badge_widths = [badge.sizeHint().width() for _c, badge, _l, _a in visible]
        fixed = sum(badge_widths) + spacing * len(visible)
        while visible and fixed > available:
            fixed -= badge_widths.pop() + spacing
            visible.pop()
        visible_chips = {id(chip) for chip, _b, _l, _a in visible}
        for chip, _badge, _label, _action in self._chips:
            chip.setVisible(id(chip) in visible_chips)

        # Whatever is left is shared between the action texts, shortest first so
        # that surplus from cheap ones flows to the longer ones.
        budget = max(0, available - fixed)
        entries = []
        for _chip, _badge, label, action in visible:
            metrics = QFontMetrics(label.font())
            entries.append(
                (metrics.horizontalAdvance(action) + self._CHIP_SPACING,
                 label, action, metrics)
            )
        slots = len(entries)
        for wanted, label, action, metrics in sorted(entries, key=lambda e: e[0]):
            granted = min(wanted, budget // slots) if slots else 0
            slots -= 1
            text_width = granted - self._CHIP_SPACING
            if text_width < metrics.horizontalAdvance("…"):
                label.setVisible(False)
                continue
            budget -= granted
            label.setVisible(True)
            label.setText(
                action
                if granted >= wanted
                else metrics.elidedText(action, Qt.TextElideMode.ElideRight, text_width)
            )


class GamepadHelpOverlay(QWidget):
    """Translucent full-window panel listing every gamepad binding."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._panel = QWidget(self)
        self._panel.setObjectName("gamepadHelpPanel")
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(28, 22, 28, 22)
        panel_layout.setSpacing(14)

        self._title = QLabel("Gamepad controls")
        panel_layout.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignCenter)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self._badges: list[QLabel] = []
        self._descriptions: list[QLabel] = []
        for row, (button, action) in enumerate(BINDINGS):
            badge = _make_badge(button, self._panel)
            description = QLabel(action, self._panel)
            self._badges.append(badge)
            self._descriptions.append(description)
            grid.addWidget(badge, row, 0)
            grid.addWidget(description, row, 1)
        panel_layout.addLayout(grid)

        self._footer = QLabel("Press B or Start to close")
        panel_layout.addWidget(self._footer, alignment=Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(self._panel)
        self.refresh_theme_colors()

    def refresh_theme_colors(self) -> None:
        """Re-apply palette-derived colours after the theme changed."""
        accent = accent_color(self)
        panel = surface_color(self)
        text = self.palette().color(QPalette.ColorRole.WindowText)
        self._panel.setStyleSheet(
            "#gamepadHelpPanel {"
            f"background-color: rgba({panel.red()}, {panel.green()}, {panel.blue()}, 240);"
            f"border: 1px solid rgba({accent.red()}, {accent.green()}, {accent.blue()}, 160);"
            "border-radius: 12px;"
            "}"
        )
        self._title.setStyleSheet(
            f"color: {text.name()}; font-size: 18px; font-weight: bold;"
        )
        badge_style = _badge_style(self._panel)
        for badge in self._badges:
            badge.setStyleSheet(badge_style)
        for description in self._descriptions:
            description.setStyleSheet(f"color: {text.name()};")
        self._footer.setStyleSheet(
            f"color: {muted_text_color(self._panel)}; font-size: 11px;"
        )

    def showEvent(self, event: Any) -> None:  # noqa: ANN401, D102 - Qt override
        # Stylesheet-driven palette colours only resolve once Qt has polished
        # the widget, so the panel is coloured on the way in, not at build time.
        super().showEvent(event)
        self.refresh_theme_colors()

    def paintEvent(self, event: Any) -> None:  # noqa: ANN401
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
        painter.end()

    def toggle(self) -> bool:
        """Show or hide the overlay; returns the new visibility."""
        if self.isVisible():
            self.hide()
            return False
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        return True
