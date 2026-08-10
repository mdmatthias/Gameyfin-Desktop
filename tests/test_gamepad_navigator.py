"""Tests for the gamepad → Qt navigation layer."""

import pytest
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QScrollBar, QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from gameyfin_frontend.gamepad import GamepadState
from gameyfin_frontend.gamepad_navigator import GamepadNavigator


class FakeManager(QObject):
    """Stands in for GamepadManager so tests can drive input directly."""

    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    button_pressed = pyqtSignal(str)
    button_released = pyqtSignal(str)
    navigate = pyqtSignal(str)
    polled = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.deadzone = 0.25
        self.device_name = "Fake Pad"
        self._connected = True

    def is_connected(self):
        return self._connected


class GridWindow(QWidget):
    """A 2x2 button grid — the simplest thing that has real geometry."""

    def __init__(self):
        super().__init__()
        self.resize(400, 300)
        layout = QGridLayout(self)
        self.top_left = QPushButton("top-left")
        self.top_right = QPushButton("top-right")
        self.bottom_left = QPushButton("bottom-left")
        self.bottom_right = QPushButton("bottom-right")
        layout.addWidget(self.top_left, 0, 0)
        layout.addWidget(self.top_right, 0, 1)
        layout.addWidget(self.bottom_left, 1, 0)
        layout.addWidget(self.bottom_right, 1, 1)


class TabWindow(QWidget):
    """Mimics the main window's tab_widget attribute."""

    def __init__(self):
        super().__init__()
        self.resize(500, 400)
        self.tab_widget = QTabWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.tab_widget)

        self.first_button = QPushButton("first")
        self.second_button = QPushButton("second")
        self.tab_widget.addTab(self.first_button, "One")
        self.tab_widget.addTab(self.second_button, "Two")


@pytest.fixture()
def manager():
    return FakeManager()


def focused(window):
    """The widget the window considers focused (works without window activation)."""
    return window.focusWidget()


def make_navigator(qtbot, window, manager):
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    navigator = GamepadNavigator(window, manager)
    # Window activation is unreliable on the offscreen platform.
    navigator.ignore_when_inactive = False
    navigator._active_window = lambda: window
    return navigator


@pytest.fixture()
def grid(qtbot, manager):
    window = GridWindow()
    navigator = make_navigator(qtbot, window, manager)
    return navigator, window


class TestCandidates:
    def test_visible_enabled_widgets_are_candidates(self, grid):
        navigator, window = grid
        found = navigator.candidates(window)
        assert set(found) >= {window.top_left, window.top_right,
                              window.bottom_left, window.bottom_right}

    def test_disabled_widgets_are_skipped(self, grid):
        navigator, window = grid
        window.top_right.setEnabled(False)
        assert window.top_right not in navigator.candidates(window)

    def test_hidden_widgets_are_skipped(self, grid):
        navigator, window = grid
        window.bottom_left.hide()
        assert window.bottom_left not in navigator.candidates(window)

    def test_labels_are_not_candidates(self, qtbot, manager):
        window = GridWindow()
        label = QLabel("just text", window)
        navigator = make_navigator(qtbot, window, manager)
        assert label not in navigator.candidates(window)

    def test_scroll_area_content_widget_is_not_a_candidate(self, qtbot, manager):
        """The QScrollArea's contentWidget must not be a focus candidate.

        The content widget is a generic QWidget whose large bounding box would
        otherwise win every spatial-score and trap gamepad navigation.
        """
        window = QWidget()
        window.resize(400, 300)
        layout = QVBoxLayout(window)

        area = QScrollArea(window)
        area.setWidgetResizable(True)
        content = QWidget()
        content.setLayout(QVBoxLayout())
        area.setWidget(content)
        layout.addWidget(area)

        checkbox = QCheckBox("scrollable", content)
        content.layout().addWidget(checkbox)

        navigator = make_navigator(qtbot, window, manager)
        found = navigator.candidates(window)
        assert checkbox in found
        assert content not in found
        assert area not in found


class TestSpatialNavigation:
    @pytest.mark.parametrize(
        ("start", "direction", "expected"),
        [
            ("top_left", "right", "top_right"),
            ("top_left", "down", "bottom_left"),
            ("bottom_right", "left", "bottom_left"),
            ("bottom_right", "up", "top_right"),
        ],
    )
    def test_neighbour_lookup(self, grid, start, direction, expected):
        navigator, window = grid
        current = getattr(window, start)
        assert navigator.find_neighbour(window, current, direction) is getattr(window, expected)

    def test_no_neighbour_at_the_edge(self, grid):
        navigator, window = grid
        assert navigator.find_neighbour(window, window.top_left, "up") is None

    def test_navigate_moves_focus(self, grid, manager):
        navigator, window = grid
        window.top_left.setFocus()

        manager.navigate.emit("right")

        assert focused(window) is window.top_right

    def test_navigate_without_focus_picks_the_first_candidate(self, grid):
        navigator, window = grid

        assert navigator.find_neighbour(window, None, "down") is window.top_left

    def test_disabled_navigator_ignores_input(self, grid, manager):
        navigator, window = grid
        window.top_left.setFocus()
        navigator.set_enabled(False)

        manager.navigate.emit("right")

        assert focused(window) is window.top_left


class TestActivation:
    def test_a_clicks_the_focused_button(self, qtbot, grid, manager):
        navigator, window = grid
        clicks = []
        window.top_left.clicked.connect(lambda: clicks.append(True))
        window.top_left.setFocus()

        manager.button_pressed.emit("a")

        # Activation animates the press, so the click lands a moment later.
        qtbot.waitUntil(lambda: clicks == [True], timeout=1000)

    def test_a_toggles_a_checkbox(self, qtbot, manager):
        window = GridWindow()
        checkbox = QCheckBox("enable", window)
        navigator = make_navigator(qtbot, window, manager)
        checkbox.setFocus()

        manager.button_pressed.emit("a")

        qtbot.waitUntil(checkbox.isChecked, timeout=1000)

    def test_a_confirms_a_combo_box_selection_and_closes_its_popup(self, qtbot, manager):
        """Confirming an open combo box popup must fully close it — including
        Qt's global active-popup-widget bookkeeping — and must not emit
        ``activated`` synchronously.

        A handler connected to ``activated`` may open a modal dialog. If that
        happened synchronously here, the dialog's exec() would nest inside
        this same call (poll timer -> button handler -> combo activation),
        holding it suspended for the dialog's entire lifetime — exactly the
        bug this guards against. Buttons dodge this because animateClick()
        already defers the real click; combo box confirmation must do the
        same.
        """
        window = QWidget()
        window.resize(300, 120)
        layout = QVBoxLayout(window)
        combo = QComboBox()
        combo.addItems(["Manage", "Shortcuts", "Config", "Delete"])
        layout.addWidget(combo)
        navigator = make_navigator(qtbot, window, manager)

        combo.setFocus()
        combo.showPopup()
        combo.view().setCurrentIndex(combo.view().model().index(1, 0))
        assert QApplication.activePopupWidget() is not None

        selected = []
        combo.activated.connect(selected.append)

        manager.button_pressed.emit("a")

        # The popup and Qt's global popup bookkeeping must be torn down
        # immediately, not deferred along with the signal.
        assert QApplication.activePopupWidget() is None
        assert not combo.view().isVisible()
        assert selected == []  # not emitted synchronously

        qtbot.waitUntil(lambda: selected == [1], timeout=1000)
        assert combo.currentIndex() == 1

    def test_down_moves_the_highlight_inside_an_open_combo_popup(self, qtbot, manager):
        """Directional input while a combo box popup is open must move the
        popup's own selection, not the outer widget focus, and must not
        confirm/activate a row on its own.

        ``QApplication.focusWidget()`` reports the combo box itself while its
        popup is open, not the internal list view — so the combo box lookup
        has to work from the combo box being the focused widget, not just
        from something living inside the popup. Sending the key to the combo
        box directly (instead of its view) would hit QComboBox's own
        keyPressEvent, whose closed-popup behaviour is to cycle to the next
        item *and* fire ``activated`` immediately — moving the highlight
        must not have that side effect while the popup is still open.
        """
        window = QWidget()
        window.resize(300, 120)
        layout = QVBoxLayout(window)
        combo = QComboBox()
        combo.addItems(["Manage", "Shortcuts", "Config", "Delete"])
        layout.addWidget(combo)
        navigator = make_navigator(qtbot, window, manager)

        activated = []
        combo.activated.connect(activated.append)

        combo.setFocus()
        combo.showPopup()
        assert combo.view().currentIndex().row() == 0

        manager.navigate.emit("down")
        manager.navigate.emit("down")

        assert combo.view().currentIndex().row() == 2
        # The popup must still be open — this is highlight movement, not a
        # confirmed selection, even though QComboBox tracks currentIndex
        # live as the highlight moves.
        assert combo.view().isVisible()
        assert activated == []


class TestValueWidgets:
    def test_left_right_adjusts_a_slider(self, qtbot, manager):
        window = QWidget()
        window.resize(300, 120)
        layout = QVBoxLayout(window)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(50)
        layout.addWidget(slider)
        navigator = make_navigator(qtbot, window, manager)
        slider.setFocus()

        manager.navigate.emit("right")

        assert slider.value() > 50
        assert focused(window) is slider

    def test_left_right_moves_focus_from_combo(self, qtbot, manager):
        """Left/right on a combo box navigates to the next/previous widget
        instead of cycling its items — the row widgets need horizontal
        navigation to reach adjacent pickers."""
        window = QWidget()
        window.resize(400, 120)
        layout = QHBoxLayout(window)
        combo_a = QComboBox()
        combo_a.addItems(["one", "two"])
        combo_b = QComboBox()
        combo_b.addItems(["alpha", "beta"])
        layout.addWidget(combo_a)
        layout.addWidget(combo_b)
        navigator = make_navigator(qtbot, window, manager)
        combo_a.setFocus()

        manager.navigate.emit("right")

        assert focused(window) is combo_b
        # The combo index must NOT have changed.
        assert combo_a.currentIndex() == 0

    def test_left_moves_focus_back_from_combo(self, qtbot, manager):
        """Left on a combo box moves focus to the previous widget."""
        window = QWidget()
        window.resize(400, 120)
        layout = QHBoxLayout(window)
        combo_a = QComboBox()
        combo_a.addItems(["one", "two"])
        combo_b = QComboBox()
        combo_b.addItems(["alpha", "beta"])
        layout.addWidget(combo_a)
        layout.addWidget(combo_b)
        navigator = make_navigator(qtbot, window, manager)
        combo_b.setFocus()

        manager.navigate.emit("left")

        assert focused(window) is combo_a
        assert combo_b.currentIndex() == 0


class RowWindow(QWidget):
    """A list whose rows are widgets with their own buttons (Downloads/Prefixes).

    The list is nested inside a scroll area exactly like the Downloads tab, so
    the tests cover both containers.
    """

    def __init__(self, rows=3, selection=QAbstractItemView.SelectionMode.SingleSelection):
        super().__init__()
        self.resize(500, 400)
        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(selection)
        content_layout.addWidget(self.list_widget)
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area)

        self.row_buttons = []
        for index in range(rows):
            item = QListWidgetItem(self.list_widget)
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            primary = QPushButton(f"primary {index}")
            secondary = QPushButton(f"secondary {index}")
            row_layout.addWidget(primary)
            row_layout.addWidget(secondary)
            item.setSizeHint(row_widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row_widget)
            self.row_buttons.append((primary, secondary))


class TestListsWithRowWidgets:
    def test_the_list_itself_is_not_a_focus_stop(self, qtbot, manager):
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)

        found = navigator.candidates(window)

        assert window.list_widget not in found
        assert window.row_buttons[0][0] in found

    def test_the_scroll_area_is_not_a_focus_stop(self, qtbot, manager):
        """Otherwise the ring wraps the whole tab and nothing inside is reachable."""
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)

        assert window.scroll_area not in navigator.candidates(window)

    def test_only_the_row_buttons_are_candidates(self, qtbot, manager):
        window = RowWindow(rows=2)
        navigator = make_navigator(qtbot, window, manager)

        found = navigator.candidates(window)

        expected = [button for row in window.row_buttons for button in row]
        assert sorted(found, key=id) == sorted(expected, key=id)

    def test_an_empty_list_offers_nothing_to_focus(self, qtbot, manager):
        """An empty Downloads tab must not fall back to ringing the container."""
        window = RowWindow(rows=0)
        navigator = make_navigator(qtbot, window, manager)

        assert navigator.candidates(window) == []

    def test_scroll_bars_are_never_focused(self, qtbot, manager):
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)

        assert not any(isinstance(w, QScrollBar) for w in navigator.candidates(window))

    def test_focus_stuck_on_a_container_steps_inside(self, qtbot, manager):
        """Qt may focus the scroll area on its own; navigation must escape it."""
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)

        target = navigator.find_neighbour(window, window.scroll_area, "down")

        assert target is window.row_buttons[0][0]

    def test_navigation_reaches_row_buttons(self, qtbot, manager):
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)
        first, second = window.row_buttons[0]
        first.setFocus()

        manager.navigate.emit("right")

        assert focused(window) is second

    def test_selection_follows_focus(self, qtbot, manager):
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)

        navigator.focus_widget(window.row_buttons[1][0])

        assert window.list_widget.currentRow() == 1

    def test_selection_is_left_alone_when_the_list_has_none(self, qtbot, manager):
        window = RowWindow(selection=QAbstractItemView.SelectionMode.NoSelection)
        navigator = make_navigator(qtbot, window, manager)

        navigator.focus_widget(window.row_buttons[1][0])

        assert window.list_widget.selectedItems() == []

    def test_list_item_lookup(self, qtbot, manager):
        window = RowWindow()
        navigator = make_navigator(qtbot, window, manager)

        view, item = navigator.list_item_for(window.row_buttons[2][1])

        assert view is window.list_widget
        assert window.list_widget.row(item) == 2

    def test_no_selection_list_skipped_even_when_children_collapse(self, qtbot, manager):
        """A NoSelection list with row widgets must never become a focus stop.

        The Prefixes tab uses NoSelection + item widgets containing QComboBox
        pickers.  If the list itself is focusable the navigator lands on it and
        the row widgets become unreachable.
        """
        window = RowWindow(selection=QAbstractItemView.SelectionMode.NoSelection)
        navigator = make_navigator(qtbot, window, manager)

        found = navigator.candidates(window)

        assert window.list_widget not in found
        assert window.row_buttons[0][0] in found
        assert window.row_buttons[0][1] in found


class TestPlainList:
    def test_plain_list_is_a_focus_stop(self, qtbot, manager):
        window = QWidget()
        window.resize(300, 200)
        layout = QVBoxLayout(window)
        list_widget = QListWidget()
        list_widget.addItems(["a", "b", "c"])
        layout.addWidget(list_widget)
        navigator = make_navigator(qtbot, window, manager)

        assert list_widget in navigator.candidates(window)

    def test_up_down_moves_the_current_row(self, qtbot, manager):
        window = QWidget()
        window.resize(300, 200)
        layout = QVBoxLayout(window)
        list_widget = QListWidget()
        list_widget.addItems(["a", "b", "c"])
        list_widget.setCurrentRow(0)
        layout.addWidget(list_widget)
        navigator = make_navigator(qtbot, window, manager)
        list_widget.setFocus()

        manager.navigate.emit("down")

        assert list_widget.currentRow() == 1
        assert focused(window) is list_widget

    def test_focus_leaves_the_list_at_its_last_row(self, qtbot, manager):
        window = QWidget()
        window.resize(300, 300)
        layout = QVBoxLayout(window)
        list_widget = QListWidget()
        list_widget.addItems(["a", "b"])
        list_widget.setCurrentRow(1)
        layout.addWidget(list_widget)
        below = QPushButton("below")
        layout.addWidget(below)
        navigator = make_navigator(qtbot, window, manager)
        list_widget.setFocus()

        manager.navigate.emit("down")

        assert focused(window) is below


class TestTabs:
    def test_shoulder_buttons_switch_tabs(self, qtbot, manager):
        window = TabWindow()
        navigator = make_navigator(qtbot, window, manager)

        manager.button_pressed.emit("rb")
        assert window.tab_widget.currentIndex() == 1

        manager.button_pressed.emit("lb")
        assert window.tab_widget.currentIndex() == 0

    def test_tab_switching_wraps_around(self, qtbot, manager):
        window = TabWindow()
        navigator = make_navigator(qtbot, window, manager)

        manager.button_pressed.emit("lb")

        assert window.tab_widget.currentIndex() == 1

    def test_back_returns_to_the_first_tab(self, qtbot, manager):
        window = TabWindow()
        navigator = make_navigator(qtbot, window, manager)
        window.tab_widget.setCurrentIndex(1)

        manager.button_pressed.emit("b")

        assert window.tab_widget.currentIndex() == 0


class TestDialogs:
    def test_back_rejects_a_dialog(self, qtbot, manager):
        window = GridWindow()
        navigator = make_navigator(qtbot, window, manager)

        dialog = QDialog(window)
        dialog.resize(200, 120)
        QPushButton("ok", dialog)
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitExposed(dialog)
        navigator._active_window = lambda: dialog

        manager.button_pressed.emit("b")

        assert not dialog.isVisible()

    def test_dialog_widgets_are_navigable(self, qtbot, manager):
        window = GridWindow()
        navigator = make_navigator(qtbot, window, manager)

        dialog = QDialog(window)
        dialog.resize(300, 200)
        layout = QVBoxLayout(dialog)
        first = QPushButton("first")
        second = QPushButton("second")
        layout.addWidget(first)
        layout.addWidget(second)
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitExposed(dialog)
        navigator._active_window = lambda: dialog
        first.setFocus()

        manager.navigate.emit("down")

        assert focused(dialog) is second


class ScrollWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(300, 200)
        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.buttons = []
        for index in range(30):
            button = QPushButton(f"row {index}")
            content_layout.addWidget(button)
            self.buttons.append(button)
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area)


class TestScrolling:
    def test_right_stick_scrolls(self, qtbot, manager):
        window = ScrollWindow()
        navigator = make_navigator(qtbot, window, manager)
        window.buttons[0].setFocus()
        bar = window.scroll_area.verticalScrollBar()
        start = bar.value()

        manager.polled.emit(GamepadState(right_y=1.0))

        assert bar.value() > start

    def test_right_stick_inside_the_deadzone_does_nothing(self, qtbot, manager):
        window = ScrollWindow()
        navigator = make_navigator(qtbot, window, manager)
        window.buttons[0].setFocus()
        bar = window.scroll_area.verticalScrollBar()
        start = bar.value()

        manager.polled.emit(GamepadState(right_y=0.1))

        assert bar.value() == start

    def test_trigger_pages_down(self, qtbot, manager):
        window = ScrollWindow()
        navigator = make_navigator(qtbot, window, manager)
        window.buttons[0].setFocus()
        bar = window.scroll_area.verticalScrollBar()
        start = bar.value()

        manager.button_pressed.emit("rt")

        assert bar.value() > start

    def test_focused_widget_is_scrolled_into_view(self, qtbot, manager):
        window = ScrollWindow()
        navigator = make_navigator(qtbot, window, manager)
        bar = window.scroll_area.verticalScrollBar()

        navigator.focus_widget(window.buttons[-1])

        assert bar.value() > 0


class TestKeyboard:
    def test_x_opens_the_keyboard_for_a_text_field(self, qtbot, manager, monkeypatch):
        window = QWidget()
        window.resize(300, 120)
        layout = QVBoxLayout(window)
        edit = QLineEdit("hello")
        layout.addWidget(edit)
        navigator = make_navigator(qtbot, window, manager)
        edit.setFocus()

        captured = {}

        def fake_get_text(parent, **kwargs):
            captured.update(kwargs)
            return "typed"

        monkeypatch.setattr(
            "gameyfin_frontend.gamepad_navigator.OnScreenKeyboard.get_text",
            staticmethod(fake_get_text),
        )

        manager.button_pressed.emit("x")

        assert captured["initial_text"] == "hello"
        assert edit.text() == "typed"

    def test_cancelling_the_keyboard_leaves_the_field_alone(self, qtbot, manager, monkeypatch):
        window = QWidget()
        window.resize(300, 120)
        layout = QVBoxLayout(window)
        edit = QLineEdit("hello")
        layout.addWidget(edit)
        navigator = make_navigator(qtbot, window, manager)
        edit.setFocus()

        monkeypatch.setattr(
            "gameyfin_frontend.gamepad_navigator.OnScreenKeyboard.get_text",
            staticmethod(lambda parent, **kwargs: None),
        )

        manager.button_pressed.emit("x")

        assert edit.text() == "hello"

    def test_x_on_a_button_does_nothing(self, grid, manager, monkeypatch):
        navigator, window = grid
        calls = []
        monkeypatch.setattr(
            "gameyfin_frontend.gamepad_navigator.OnScreenKeyboard.get_text",
            staticmethod(lambda parent, **kwargs: calls.append(True)),
        )
        window.top_left.setFocus()

        manager.button_pressed.emit("x")

        assert calls == []


class TestHelpAndMouseMode:
    def test_start_toggles_the_help_overlay(self, grid, manager):
        navigator, window = grid

        manager.button_pressed.emit("start")
        assert navigator.help_overlay.isVisible()

        manager.button_pressed.emit("start")
        assert not navigator.help_overlay.isVisible()

    def test_help_overlay_swallows_navigation(self, grid, manager):
        navigator, window = grid
        window.top_left.setFocus()
        manager.button_pressed.emit("start")

        manager.navigate.emit("right")

        assert focused(window) is window.top_left

    def test_back_button_toggles_mouse_mode(self, grid, manager):
        navigator, _ = grid

        manager.button_pressed.emit("back")
        assert navigator.mouse_mode is True

        manager.button_pressed.emit("back")
        assert navigator.mouse_mode is False


class TestTextWidgets:
    def test_a_multiline_edit_is_focusable(self, qtbot, manager):
        """QPlainTextEdit is a scroll area, but it is a leaf control, not a container."""
        window = QWidget()
        window.resize(400, 300)
        layout = QVBoxLayout(window)
        editor = QPlainTextEdit()
        layout.addWidget(editor)
        navigator = make_navigator(qtbot, window, manager)

        assert editor in navigator.candidates(window)

    def test_a_multiline_edit_opens_the_keyboard(self, qtbot, manager, monkeypatch):
        window = QWidget()
        window.resize(400, 300)
        layout = QVBoxLayout(window)
        editor = QPlainTextEdit()
        editor.setPlainText("KEY=VALUE")
        layout.addWidget(editor)
        navigator = make_navigator(qtbot, window, manager)
        editor.setFocus()

        captured = {}

        def fake_get_text(parent, **kwargs):
            captured.update(kwargs)
            return "OTHER=1"

        monkeypatch.setattr(
            "gameyfin_frontend.gamepad_navigator.OnScreenKeyboard.get_text",
            staticmethod(fake_get_text),
        )

        manager.button_pressed.emit("x")

        assert captured["initial_text"] == "KEY=VALUE"
        assert captured["multiline"] is True
        assert editor.toPlainText() == "OTHER=1"


class TestFocusRing:
    def test_ring_does_not_cover_the_widget_it_outlines(self, qtbot):
        """qt-material paints every plain QWidget opaque; the ring must not hide
        the control it points at."""
        from PyQt6.QtGui import QColor

        from gameyfin_frontend.gamepad_navigator import FocusRing

        host = QWidget()
        host.resize(300, 200)
        layout = QVBoxLayout(host)
        target = QWidget()
        target.setStyleSheet("background-color: #ff0000;")
        layout.addWidget(target)
        qtbot.addWidget(host)
        host.show()
        qtbot.waitExposed(host)

        ring = FocusRing(host)
        ring.follow(target)
        qtbot.wait(20)

        image = host.grab().toImage()
        centre = target.mapTo(host, target.rect().center())
        assert QColor(image.pixel(centre.x(), centre.y())).name() == "#ff0000"

    def test_ring_band_is_painted(self, qtbot):
        from PyQt6.QtGui import QColor

        from gameyfin_frontend.gamepad_navigator import FocusRing

        host = QWidget()
        host.resize(300, 200)
        layout = QVBoxLayout(host)
        target = QWidget()
        target.setStyleSheet("background-color: #ff0000;")
        layout.addWidget(target)
        qtbot.addWidget(host)
        host.show()
        qtbot.waitExposed(host)

        ring = FocusRing(host)
        ring.follow(target)
        qtbot.wait(20)

        image = host.grab().toImage()
        top_left = target.mapTo(host, target.rect().topLeft())
        centre_x = target.mapTo(host, target.rect().center()).x()
        band = QColor(image.pixel(centre_x, top_left.y() - FocusRing.MARGIN + 1))
        assert band.name() == "#00bcd4"


    def test_ring_follows_the_focused_widget(self, qtbot, grid, manager):
        navigator, window = grid

        navigator.focus_widget(window.bottom_right)

        # Ring updates are deferred onto the event loop on purpose — touching
        # widget visibility inside Qt's focus handling corrupts painting.
        qtbot.waitUntil(navigator.focus_ring.isVisible, timeout=1000)
        assert navigator.focus_ring.geometry().contains(
            window.bottom_right.geometry().center()
        )

    def test_ring_updates_are_deferred(self, grid):
        navigator, window = grid

        navigator.focus_widget(window.bottom_right)

        assert navigator.focus_ring.isVisible() is False

    def test_ring_is_hidden_without_a_controller(self, qtbot, grid, manager):
        navigator, window = grid
        manager._connected = False

        navigator.focus_widget(window.top_left)
        qtbot.wait(50)

        assert not navigator.focus_ring.isVisible()
