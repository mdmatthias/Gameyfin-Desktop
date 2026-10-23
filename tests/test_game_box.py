"""Tests for the 3D big-box detail view."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QImage

from gameyfin_frontend.config import BOX_REST_YAW
from gameyfin_frontend.services.gameyfin_api import Game, GameImage
from gameyfin_frontend.widgets.game_box import (BackCover, DescriptionPage, GameBoxView,
                                                _finish_texture, _font_to_fit, _format_release, _plain_text, edge_color, render_back_cover,
                                                render_inner_flap, render_spine)
from gameyfin_frontend.widgets.game_detail import GameDetailWidget

# Painting text into images needs a live QApplication even without qtbot
pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture()
def game():
    return Game(id=7, title="Gamma", library_id=1, summary="<p>A <b>bold</b> game</p>",
                release="2004-11-16", user_rating=95, platforms=["WINDOWS"],
                genres=["Shooter"], developers=["Valve"], publishers=["Valve"],
                file_size=4096, cover=GameImage(id=71, type="COVER"),
                images=[GameImage(id=72, type="SCREENSHOT"), GameImage(id=73, type="SCREENSHOT")])


def _image(width, height, colour="red"):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(colour))
    return image


@pytest.fixture()
def png_bytes(qtbot):
    """Bytes of a small real PNG (encoded by Qt itself)."""
    from PyQt6.QtCore import QBuffer

    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert _image(16, 9).save(buffer, "PNG")
    return bytes(buffer.data())


@pytest.fixture()
def box(qtbot):
    view = GameBoxView()
    qtbot.addWidget(view)
    view.resize(800, 700)
    return view


class TestTextures:
    def test_plain_text_strips_html(self):
        assert _plain_text("<p>A <b>bold</b> &amp; brave</p>") == "A bold & brave"
        assert _plain_text(None) == ""

    def test_edge_color_is_dark_enough_for_white_text(self):
        colour = edge_color(_image(100, 140, "white"))
        assert colour.lightness() <= 105

    def test_back_cover_reports_one_rect_per_screenshot(self, game):
        back = render_back_cover(game, 400, 560, QColor("navy"), None,
                                 [_image(160, 90), _image(160, 90, "blue")])

        assert back.image.size().width() == 400
        assert len(back.screenshot_rects) == 2
        for rect in back.screenshot_rects:
            assert QRectF(0, 0, 1, 1).contains(rect)

    def test_long_blurb_is_clickable(self, game):
        game = replace(game, summary="word " * 600)
        back = render_back_cover(game, 400, 560, QColor("navy"), None, [_image(160, 90)])

        assert back.description_rect is not None
        assert QRectF(0, 0, 1, 1).contains(back.description_rect)

    def test_short_blurb_is_not_clickable(self, game):
        back = render_back_cover(game, 400, 560, QColor("navy"), None, [])
        assert back.description_rect is None

    def test_back_cover_without_screenshots(self, game):
        back = render_back_cover(game, 400, 560, QColor("navy"), None, [])
        assert back.screenshot_rects == []

    def test_inner_flap_renders(self, game):
        flap = render_inner_flap(game, 400, 560, QColor("navy"), _image(160, 90))
        assert (flap.width(), flap.height()) == (400, 560)

    def test_spine_has_requested_size(self, game):
        spine = render_spine(game, 80, 560, QColor("navy"))
        assert (spine.width(), spine.height()) == (80, 560)

    def test_short_title_keeps_the_full_font_size(self):
        assert _font_to_fit("DOOM", 32, 400, bold=True).pixelSize() == 32

    def test_long_title_shrinks_until_it_fits(self):
        title = "DEUS EX: HUMAN REVOLUTION - DIRECTOR'S CUT"
        font = _font_to_fit(title, 32, 300, bold=True)

        assert font.pixelSize() < 32
        assert QFontMetricsF(font).horizontalAdvance(title) <= 300

    def test_two_lines_keep_a_bigger_font_than_one(self):
        title = "Deus Ex: Human Revolution - Director's Cut"
        one_line = _font_to_fit(title, 32, 300, bold=True)
        two_lines = _font_to_fit(title, 32, 300, bold=True, lines=2)

        assert two_lines.pixelSize() > one_line.pixelSize()
        metrics = QFontMetricsF(two_lines)
        wrapped = metrics.boundingRect(QRectF(0, 0, 300, 1e6), int(Qt.TextFlag.TextWordWrap), title)
        assert wrapped.height() <= 2 * metrics.lineSpacing() + 1


class TestGameBoxView:
    def test_cover_aspect_shapes_the_box(self, box):
        box.set_front(_image(300, 450), QColor("navy"))
        assert box.aspect == pytest.approx(1.5)

    def test_flip_turns_to_the_back_and_signals(self, qtbot, box):
        box.reset_pose(intro=False)
        assert not box.is_showing_back()

        with qtbot.waitSignal(box.side_changed, timeout=3000) as blocker:
            box.flip()
        assert blocker.args == [True]
        assert box.is_showing_back()

    def test_paints_at_any_angle(self, box):
        box.set_front(_image(300, 420), QColor("navy"))
        for yaw in (0, 45, 90, 135, 180, 270, 359):
            box.yaw = yaw
            assert not box.grab().isNull()

    def test_screenshot_hit_test_on_back(self, box):
        box.set_back(BackCover(_image(400, 560), [QRectF(0.0, 0.0, 1.0, 1.0)]))
        box.yaw, box.pitch = 180.0, 0.0
        centre = QPointF(box.width() / 2, box.height() * 0.47)

        assert box.screenshot_at(centre) == 0

        box.yaw = 0.0  # front facing: the back's screenshots aren't clickable
        assert box.screenshot_at(centre) is None


    def test_description_hit_test_on_back(self, box):
        box.set_back(BackCover(_image(400, 560), [], QRectF(0.0, 0.0, 1.0, 1.0)))
        box.yaw, box.pitch = 180.0, 0.0
        centre = QPointF(box.width() / 2, box.height() * 0.47)

        assert box.description_at(centre)
        assert box.has_more_description()


    def _open_instantly(self, qtbot, box):
        box.set_inside(_image(400, 560), DescriptionPage("T", "word " * 2000, 400, 560, QColor("navy")))
        box.open_cover()
        qtbot.waitUntil(lambda: box.open == 1.0, timeout=3000)

    def test_clicking_the_cover_opens_it(self, qtbot, box):
        box.set_front(_image(300, 420), QColor("navy"))
        box.reset_pose(intro=False)
        centre = QPointF(box.width() / 2, box.height() * 0.47)

        with qtbot.waitSignal(box.cover_changed, timeout=1000) as blocker:
            box.click_at(centre)
        assert blocker.args == [True]
        assert box.is_cover_open()

    def test_opening_from_the_back_turns_round_before_the_cover_moves(self, qtbot, box):
        box.set_inside(_image(400, 560), DescriptionPage("T", "word", 400, 560, QColor("navy")))
        box.yaw = BOX_REST_YAW + 180.0
        seen_open_while_turned = []

        def record(_value):
            away = (box.yaw - BOX_REST_YAW) % 360
            if min(away, 360 - away) > 45:
                seen_open_while_turned.append(box.open)

        box._animation.valueChanged.connect(record)

        assert box.open_cover() is True
        qtbot.waitUntil(lambda: box.open == 1.0, timeout=5000)

        assert seen_open_while_turned and max(seen_open_while_turned) == 0.0

    def test_opening_from_the_front_does_not_turn_first(self, box):
        box.reset_pose(intro=False)
        assert box.open_cover() is False

    def test_open_cover_only_adds_a_thin_shadow_along_its_edge(self, qtbot, box):
        assert len(box._footprints()) == 1

        self._open_instantly(qtbot, box)
        base, cover = box._footprints()

        # The cover stands on its edge: no shadow fills the floor under it
        assert cover[2] < base[2] / 10

    def test_finished_textures_keep_their_size(self, box):
        finished = _finish_texture(_image(120, 200), "front")
        assert (finished.width(), finished.height()) == (120, 200)

    def test_open_book_shows_both_pages(self, qtbot, box):
        self._open_instantly(qtbot, box)
        names = [name for name, _, _ in box._scene()]

        assert "page" in names and "inside" in names
        assert "front" not in names  # the cover's outside faces away now

    def test_long_description_scrolls_only_while_open(self, qtbot, box):
        box.set_inside(_image(400, 560), DescriptionPage("T", "word " * 2000, 400, 560, QColor("navy")))
        assert not box.scroll_page(100)

        self._open_instantly(qtbot, box)
        assert box.gamepad_scroll(0, 100)
        assert box._page_scroll > 0

    def test_clicking_outside_the_page_closes(self, qtbot, box):
        self._open_instantly(qtbot, box)
        box.click_at(QPointF(2, 2))
        assert not box.is_cover_open()

    def test_flip_closes_the_cover(self, qtbot, box):
        self._open_instantly(qtbot, box)
        box.flip()
        assert not box.is_cover_open()

    def test_short_description_fits_without_scrolling(self):
        page = DescriptionPage("T", "A short one.", 400, 560, QColor("navy"))
        assert page.max_scroll == 0
        assert not page.render(0).isNull()


class TestDetailBoxMode:
    @pytest.fixture()
    def detail(self, qtbot, fresh_settings):
        cache = MagicMock()
        cache.request.return_value = None
        widget = GameDetailWidget(cache, fresh_settings)
        qtbot.addWidget(widget)
        return widget

    def test_box_view_is_the_default(self, detail):
        assert detail.box_mode
        assert detail.pages.currentWidget() is detail.box_page
        assert detail.box_page.isAncestorOf(detail.actions_widget)

    def test_switching_moves_actions(self, detail):
        detail.set_box_mode(False)

        assert not detail.box_mode
        assert detail.pages.currentWidget() is detail.scroll
        assert detail.scroll.isAncestorOf(detail.download_button)

        detail.set_box_mode(True)
        assert detail.box_page.isAncestorOf(detail.download_button)

    def test_artwork_reaches_the_box(self, qtbot, detail, game, png_bytes):
        detail.show_game(game)
        assert detail.box_title_label.text() == "Gamma"

        detail._on_image_ready(72, png_bytes)
        qtbot.waitUntil(lambda: detail._box_shot_order == [game.images[0]], timeout=2000)

    def test_box_screenshot_click_opens_viewer(self, detail, game, png_bytes):
        detail.show_game(game)
        detail._on_image_ready(72, png_bytes)
        detail._refresh_box()

        detail._on_box_screenshot_clicked(0)

        assert detail._viewer_dialog is not None
        detail._viewer_dialog.close()

    def test_download_is_the_highlighted_action_until_installed(self, detail, game):
        detail.show_game(game)
        assert detail.download_button.styleSheet() and not detail.play_button.styleSheet()

        detail.set_installed(MagicMock(scripts=["game.sh"], last_script=None))
        assert detail.play_button.styleSheet() and not detail.download_button.styleSheet()

    def test_box_subtitle_uses_the_same_date_format_as_the_box(self, detail, game):
        detail.show_game(game)
        assert detail.box_subtitle_label.text().startswith(_format_release(game.release))

    def test_cover_button_toggles_the_box(self, qtbot, detail, game):
        detail.show_game(game)
        assert detail.cover_button.text() == "Open the box"

        detail.cover_button.click()
        assert detail.box_view.is_cover_open()
        assert detail.cover_button.text() == "Close the box"


class TestDiscAnimation:
    @pytest.fixture()
    def disc_box(self, box, game):
        from gameyfin_frontend.widgets.game_disc import render_disc

        box.set_front(_image(300, 420), QColor("navy"))
        box.set_disc(render_disc(game, _image(300, 420), 128, QColor("navy")))
        box.reset_pose(intro=False)
        return box

    def test_render_disc_has_a_hole(self, game):
        from gameyfin_frontend.widgets.game_disc import render_disc

        disc = render_disc(game, None, 128, QColor("navy"))
        assert disc.pixelColor(64, 64).alpha() == 0
        assert disc.pixelColor(64, 10).alpha() > 0

    def test_insert_opens_the_box_and_paints_every_moment(self, disc_box):
        disc_box.insert_disc()
        assert disc_box.is_inserting_disc()
        assert disc_box.is_cover_open()

        for step in range(21):
            disc_box._disc_t = step / 20
            assert not disc_box.grab().isNull()

    def test_animation_restores_a_closed_box(self, qtbot, disc_box):
        disc_box.insert_disc()
        disc_box.skip_disc()

        assert not disc_box.is_inserting_disc()
        assert not disc_box.is_cover_open()

    def test_click_skips_the_animation(self, qtbot, disc_box):
        from PyQt6.QtCore import QPoint

        disc_box.insert_disc()
        qtbot.mouseClick(disc_box, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        assert not disc_box.is_inserting_disc()

    def test_download_in_box_mode_plays_the_animation(self, qtbot, fresh_settings, game):
        from gameyfin_frontend.services.gameyfin_api import DownloadProvider

        cache = MagicMock()
        cache.request.return_value = None
        detail = GameDetailWidget(cache, fresh_settings)
        qtbot.addWidget(detail)
        detail.set_providers([DownloadProvider(key="fs", name="FS", priority=1)])
        detail.show_game(game)
        detail.show()
        qtbot.waitExposed(detail)

        with qtbot.waitSignal(detail.download_requested, timeout=1000):
            detail.download_button.click()
        assert detail.box_view.is_inserting_disc()
