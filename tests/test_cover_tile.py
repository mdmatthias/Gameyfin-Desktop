"""Tests for the cover tile delegate used by the native library grid."""

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QStyleOptionViewItem

from gameyfin_frontend.config import COVER_TILE_HEIGHT, COVER_TILE_WIDTH
from gameyfin_frontend.widgets.cover_tile import (CoverTileDelegate,
                                                  TILE_PADDING, TITLE_LINES,
                                                  tile_size_hint)


class TestTileSizeHint:
    """The tile has to fit a full cover plus two title lines and the meta line."""

    def test_width_leaves_padding_around_the_cover(self, qtbot):
        assert tile_size_hint(QFont()).width() == COVER_TILE_WIDTH + 2 * TILE_PADDING

    def test_height_reserves_room_below_the_cover(self, qtbot):
        height = tile_size_hint(QFont()).height()
        assert height > COVER_TILE_HEIGHT + 2 * TILE_PADDING

    def test_height_grows_with_the_font(self, qtbot):
        small, large = QFont(), QFont()
        small.setPointSize(8)
        large.setPointSize(16)
        assert tile_size_hint(large).height() > tile_size_hint(small).height()


class TestTitleWrapping:
    """Titles wrap to at most two lines and elide instead of overflowing."""

    metrics = None

    def _metrics(self):
        font = QFont()
        font.setPointSize(10)
        return QFontMetrics(font)

    def test_short_title_stays_on_one_line(self, qtbot):
        assert CoverTileDelegate._wrap("Alan Wake", self._metrics(), 400) == ["Alan Wake"]

    def test_empty_title_yields_no_lines(self, qtbot):
        assert CoverTileDelegate._wrap("   ", self._metrics(), 200) == []

    def test_long_title_is_clamped_to_two_lines(self, qtbot):
        lines = CoverTileDelegate._wrap(
            "Advanced Dungeons & Dragons: Secret of the Silver Blades",
            self._metrics(), 160)
        assert 1 < len(lines) <= TITLE_LINES

    def test_clamped_title_is_elided(self, qtbot):
        lines = CoverTileDelegate._wrap(
            "Advanced Dungeons & Dragons: Secret of the Silver Blades",
            self._metrics(), 120)
        assert lines[-1].endswith("…")

    def test_lines_fit_the_available_width(self, qtbot):
        metrics = self._metrics()
        lines = CoverTileDelegate._wrap(
            "Alone in the Dark: The New Nightmare", metrics, 150)
        assert all(metrics.horizontalAdvance(line) <= 150 for line in lines)

    def test_single_unbreakable_word_is_elided(self, qtbot):
        metrics = self._metrics()
        [line] = CoverTileDelegate._wrap("Supercalifragilistic" * 3, metrics, 90)
        assert metrics.horizontalAdvance(line) <= 90


class TestPainting:
    """The delegate paints every tile itself, with or without a cover."""

    def _paint(self, qtbot, *, icon: QIcon | None, state) -> QPixmap:
        view = QListWidget()
        qtbot.addWidget(view)
        delegate = CoverTileDelegate(view)
        item = QListWidgetItem("Alan Wake's American Nightmare")
        item.setData(CoverTileDelegate.META_ROLE, "2011 · 8.90 GB")
        if icon is not None:
            item.setIcon(icon)
        view.addItem(item)

        size = tile_size_hint(view.font())
        target = QPixmap(size)
        target.fill(QColor("#202020"))
        option = QStyleOptionViewItem()
        option.initFrom(view)
        option.rect = QRect(0, 0, size.width(), size.height())
        option.state = state
        painter = QPainter(target)
        delegate.paint(painter, option, view.model().index(0, 0))
        painter.end()
        return target

    def test_paints_the_cover_pixels(self, qtbot):
        cover = QPixmap(COVER_TILE_WIDTH, COVER_TILE_HEIGHT)
        cover.fill(QColor("#ff0000"))
        target = self._paint(qtbot, icon=QIcon(cover),
                             state=QStyleOptionViewItem().state)
        centre = target.toImage().pixelColor(target.width() // 2,
                                             COVER_TILE_HEIGHT // 2)
        assert centre.red() > centre.green() and centre.red() > centre.blue()

    def test_missing_cover_still_paints_a_block(self, qtbot):
        target = self._paint(qtbot, icon=None, state=QStyleOptionViewItem().state)
        background = QColor("#202020")
        centre = target.toImage().pixelColor(target.width() // 2,
                                             COVER_TILE_HEIGHT // 2)
        assert centre != background

    def test_selected_tile_differs_from_the_idle_one(self, qtbot):
        from PyQt6.QtWidgets import QStyle

        idle = self._paint(qtbot, icon=None, state=QStyleOptionViewItem().state)
        selected = self._paint(
            qtbot, icon=None,
            state=QStyleOptionViewItem().state | QStyle.StateFlag.State_Selected)
        assert idle.toImage() != selected.toImage()

    def test_selection_uses_the_theme_accent(self, monkeypatch, qtbot):
        from PyQt6.QtWidgets import QStyle

        selected_state = QStyleOptionViewItem().state | QStyle.StateFlag.State_Selected
        monkeypatch.setenv("QTMATERIAL_PRIMARYCOLOR", "#ff9800")
        warm = self._paint(qtbot, icon=None, state=selected_state)
        monkeypatch.setenv("QTMATERIAL_PRIMARYCOLOR", "#8bc34a")
        green = self._paint(qtbot, icon=None, state=selected_state)

        assert warm.toImage() != green.toImage()

    def test_size_hint_matches_the_tile_metrics(self, qtbot):
        view = QListWidget()
        qtbot.addWidget(view)
        delegate = CoverTileDelegate(view)
        option = QStyleOptionViewItem()
        option.initFrom(view)
        assert delegate.sizeHint(option, view.model().index(0, 0)) == \
            tile_size_hint(option.font)
