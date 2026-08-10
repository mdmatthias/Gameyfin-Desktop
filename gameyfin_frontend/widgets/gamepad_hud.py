"""On-screen gamepad affordances: the hint bar and the full help overlay.

Both are purely informational — they never take focus, so they cannot interfere
with gamepad navigation.
"""

from __future__ import annotations

from typing import Any, Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

ACCENT = "#00bcd4"

_BADGE_STYLE = (
    f"background-color: {ACCENT};"
    "color: #10171a;"
    "border-radius: 8px;"
    "padding: 1px 7px;"
    "font-weight: bold;"
)

# Full binding reference, also used by the help overlay.
BINDINGS: tuple[tuple[str, str], ...] = (
    ("D-pad / Left stick", "Move between items"),
    ("A", "Select / activate"),
    ("B", "Back, cancel or close"),
    ("Y", "Refresh / reload"),
    ("LB / RB", "Previous / next tab"),
    ("LT / RT", "Page up / page down"),
    ("Right stick", "Scroll"),
    ("Back", "Toggle mouse mode"),
    ("Start", "Show this help"),
)


def _make_badge(text: str) -> QLabel:
    badge = QLabel(text)
    badge.setStyleSheet(_BADGE_STYLE)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return badge


class GamepadHintBar(QWidget):
    """A slim strip of ``[button] action`` chips shown while a pad is connected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(6)
        self._status = QLabel("")
        self._status.setStyleSheet("color: palette(mid);")
        self._layout.addWidget(self._status)
        self._layout.addStretch(1)
        self._hint_widgets: list[QWidget] = []
        self.set_hints([])

    def set_status(self, text: str) -> None:
        """Set the left-hand status text (device name, mouse mode, …)."""
        self._status.setText(text)

    def set_hints(self, hints: Iterable[tuple[str, str]]) -> None:
        """Replace the chips with ``(button, action)`` pairs."""
        for widget in self._hint_widgets:
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._hint_widgets = []

        for button, action in hints:
            badge = _make_badge(button)
            label = QLabel(action)
            label.setStyleSheet("color: palette(text);")
            self._layout.addWidget(badge)
            self._layout.addWidget(label)
            self._hint_widgets.extend((badge, label))


class GamepadHelpOverlay(QWidget):
    """Translucent full-window panel listing every gamepad binding."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        panel = QWidget(self)
        panel.setObjectName("gamepadHelpPanel")
        panel.setStyleSheet(
            "#gamepadHelpPanel {"
            "background-color: rgba(20, 26, 30, 240);"
            "border: 1px solid rgba(0, 188, 212, 160);"
            "border-radius: 12px;"
            "}"
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(28, 22, 28, 22)
        panel_layout.setSpacing(14)

        title = QLabel("Gamepad controls")
        title.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        panel_layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for row, (button, action) in enumerate(BINDINGS):
            badge = _make_badge(button)
            description = QLabel(action)
            description.setStyleSheet("color: #e6edf0;")
            grid.addWidget(badge, row, 0)
            grid.addWidget(description, row, 1)
        panel_layout.addLayout(grid)

        footer = QLabel("Press B or Start to close")
        footer.setStyleSheet("color: #8fa3ab; font-size: 11px;")
        panel_layout.addWidget(footer, alignment=Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(panel)

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
