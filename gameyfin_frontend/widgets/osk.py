"""On-screen keyboard used for text entry when driving the app with a gamepad.

The keys are plain :class:`QPushButton` widgets, so the regular gamepad
navigator moves between them without any special casing.  A physical keyboard
keeps working too — printable key presses that the focused key button ignores
bubble up to :meth:`OnScreenKeyboard.keyPressEvent`.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

# Character rows, unshifted and shifted.
_ROWS_LOWER = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", "-_=.,:/@~")
_ROWS_UPPER = ("!@#$%^&*()", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM", "+?!#%&'\"\\")

_KEY_SIZE = 44


class OnScreenKeyboard(QDialog):
    """A gamepad-navigable keyboard that edits a string."""

    def __init__(
        self,
        parent: QWidget | None = None,
        initial_text: str = "",
        title: str = "Text input",
        multiline: bool = False,
        password: bool = False,
        label: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._multiline = multiline
        self._shifted = False
        self._char_buttons: list[tuple[QPushButton, int, int]] = []

        layout = QVBoxLayout(self)

        if label:
            layout.addWidget(QLabel(label))

        self.preview: Any
        if multiline:
            self.preview = QPlainTextEdit()
            self.preview.setPlainText(initial_text)
            self.preview.setMinimumHeight(90)
        else:
            self.preview = QLineEdit()
            self.preview.setText(initial_text)
            if password:
                self.preview.setEchoMode(QLineEdit.EchoMode.Password)
        self.preview.setReadOnly(True)
        # Kept out of the focus chain so the gamepad never lands on the preview.
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.preview)

        self.keys_layout = QGridLayout()
        self.keys_layout.setSpacing(4)
        layout.addLayout(self.keys_layout)
        self._build_character_keys()

        controls = QHBoxLayout()
        self.shift_button = self._make_button("Shift", self.toggle_shift, width=90)
        self.space_button = self._make_button("Space", lambda: self.insert_text(" "), width=200)
        self.backspace_button = self._make_button("⌫", self.backspace, width=90)
        self.clear_button = self._make_button("Clear", self.clear_text, width=90)
        controls.addWidget(self.shift_button)
        controls.addWidget(self.space_button)
        controls.addWidget(self.backspace_button)
        controls.addWidget(self.clear_button)
        if multiline:
            self.newline_button = self._make_button("⏎", lambda: self.insert_text("\n"), width=90)
            controls.addWidget(self.newline_button)
        layout.addLayout(controls)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = self._make_button("Cancel", self.reject, width=120)
        self.ok_button = self._make_button("OK", self.accept, width=120)
        self.ok_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.ok_button)
        layout.addLayout(actions)

        self._first_key: QPushButton | None = self._char_buttons[0][0] if self._char_buttons else None

    # -- construction helpers ---------------------------------------------

    def _make_button(self, text: str, slot: Any, width: int = _KEY_SIZE) -> QPushButton:
        button = QPushButton(text)
        button.setFixedHeight(_KEY_SIZE)
        button.setMinimumWidth(width)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def _build_character_keys(self) -> None:
        for row_index, row in enumerate(_ROWS_LOWER):
            for column, char in enumerate(row):
                button = self._make_button(char, lambda _=False, r=row_index, c=column: self._on_char_key(r, c))
                self.keys_layout.addWidget(button, row_index, column)
                self._char_buttons.append((button, row_index, column))

    # -- editing -----------------------------------------------------------

    def _char_at(self, row: int, column: int) -> str:
        source = _ROWS_UPPER if self._shifted else _ROWS_LOWER
        if row < len(source) and column < len(source[row]):
            return source[row][column]
        return ""

    def _on_char_key(self, row: int, column: int) -> None:
        char = self._char_at(row, column)
        if char:
            self.insert_text(char)

    def toggle_shift(self) -> None:
        """Switch between the lower-case and shifted key sets."""
        self._shifted = not self._shifted
        for button, row, column in self._char_buttons:
            button.setText(self._char_at(row, column))

    def insert_text(self, text: str) -> None:
        self.set_text(self.text() + text)

    def backspace(self) -> None:
        self.set_text(self.text()[:-1])

    def clear_text(self) -> None:
        self.set_text("")

    def text(self) -> str:
        if self._multiline:
            return self.preview.toPlainText()
        return self.preview.text()

    def set_text(self, value: str) -> None:
        if self._multiline:
            self.preview.setPlainText(value)
        else:
            self.preview.setText(value)

    # -- physical keyboard passthrough -------------------------------------

    def keyPressEvent(self, event: Any) -> None:  # noqa: ANN401
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._multiline and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insert_text("\n")
            else:
                self.accept()
            return
        if key == Qt.Key.Key_Backspace:
            self.backspace()
            return
        text = event.text()
        if text and text.isprintable():
            self.insert_text(text)
            return
        super().keyPressEvent(event)

    def showEvent(self, event: Any) -> None:  # noqa: ANN401
        super().showEvent(event)
        if self._first_key is not None:
            self._first_key.setFocus(Qt.FocusReason.OtherFocusReason)

    # -- convenience -------------------------------------------------------

    @staticmethod
    def get_text(
        parent: QWidget | None,
        initial_text: str = "",
        title: str = "Text input",
        multiline: bool = False,
        password: bool = False,
        label: str = "",
    ) -> str | None:
        """Show the keyboard modally and return the new text, or None if cancelled."""
        keyboard = OnScreenKeyboard(
            parent, initial_text=initial_text, title=title,
            multiline=multiline, password=password, label=label,
        )
        if keyboard.exec() == QDialog.DialogCode.Accepted:
            return keyboard.text()
        return None
