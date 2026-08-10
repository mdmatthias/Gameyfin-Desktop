"""Tests for the gamepad on-screen keyboard."""

import pytest
from PyQt6.QtWidgets import QDialog

from gameyfin_frontend.widgets.osk import OnScreenKeyboard


@pytest.fixture()
def keyboard(qtbot):
    osk = OnScreenKeyboard(initial_text="hi")
    qtbot.addWidget(osk)
    return osk


class TestEditing:
    def test_initial_text_is_shown(self, keyboard):
        assert keyboard.text() == "hi"

    def test_insert_appends(self, keyboard):
        keyboard.insert_text("!")
        assert keyboard.text() == "hi!"

    def test_backspace_removes_the_last_character(self, keyboard):
        keyboard.backspace()
        assert keyboard.text() == "h"

    def test_backspace_on_empty_text_is_safe(self, qtbot):
        osk = OnScreenKeyboard()
        qtbot.addWidget(osk)
        osk.backspace()
        assert osk.text() == ""

    def test_clear(self, keyboard):
        keyboard.clear_text()
        assert keyboard.text() == ""

    def test_preview_never_takes_focus(self, keyboard):
        """Otherwise the gamepad would land on it instead of on the keys."""
        from PyQt6.QtCore import Qt

        assert keyboard.preview.focusPolicy() == Qt.FocusPolicy.NoFocus


class TestKeys:
    def test_pressing_a_letter_key_types_it(self, keyboard):
        button = next(b for b, _, _ in keyboard._char_buttons if b.text() == "q")
        button.click()
        assert keyboard.text() == "hiq"

    def test_shift_switches_the_key_set(self, keyboard):
        button = next(b for b, _, _ in keyboard._char_buttons if b.text() == "q")

        keyboard.toggle_shift()

        assert button.text() == "Q"
        button.click()
        assert keyboard.text() == "hiQ"

    def test_shift_is_a_toggle(self, keyboard):
        keyboard.toggle_shift()
        keyboard.toggle_shift()
        button = next(b for b, _, _ in keyboard._char_buttons if b.text() == "q")
        assert button.text() == "q"

    def test_space_key(self, keyboard):
        keyboard.space_button.click()
        assert keyboard.text() == "hi "

    def test_all_keys_can_take_focus(self, keyboard):
        from PyQt6.QtCore import Qt

        for button, _, _ in keyboard._char_buttons:
            assert button.focusPolicy() & Qt.FocusPolicy.TabFocus


class TestMultiline:
    def test_newline_key_exists_only_in_multiline_mode(self, qtbot):
        single = OnScreenKeyboard()
        multi = OnScreenKeyboard(multiline=True)
        qtbot.addWidget(single)
        qtbot.addWidget(multi)

        assert not hasattr(single, "newline_button")
        multi.newline_button.click()
        assert multi.text() == "\n"

    def test_multiline_keeps_line_breaks(self, qtbot):
        osk = OnScreenKeyboard(initial_text="a\nb", multiline=True)
        qtbot.addWidget(osk)
        assert osk.text() == "a\nb"


class TestResult:
    def test_get_text_returns_the_edited_value(self, qtbot, monkeypatch):
        def accept_immediately(self):
            self.insert_text("!")
            self.setResult(QDialog.DialogCode.Accepted)
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(OnScreenKeyboard, "exec", accept_immediately)

        assert OnScreenKeyboard.get_text(None, initial_text="ok") == "ok!"

    def test_get_text_returns_none_when_cancelled(self, monkeypatch):
        monkeypatch.setattr(
            OnScreenKeyboard, "exec",
            lambda self: QDialog.DialogCode.Rejected,
        )

        assert OnScreenKeyboard.get_text(None, initial_text="ok") is None
