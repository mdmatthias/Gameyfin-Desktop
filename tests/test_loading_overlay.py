"""Tests for the LoadingOverlay fade-in/fade-out lifecycle."""

import pytest

from PyQt6.QtGui import QIcon, QPixmap

from gameyfin_frontend.widgets.loading_overlay import LoadingOverlay


@pytest.fixture()
def overlay(qtbot):
    """Return a parentless LoadingOverlay (qtbot ensures a QApplication)."""
    w = LoadingOverlay(None)
    qtbot.addWidget(w)
    return w


def _run_fade_to_completion(overlay, max_ticks=200):
    """Drive the animation timer manually until the overlay hides or ticks run out."""
    for _ in range(max_ticks):
        if not overlay.isVisible() and overlay._fading_out:
            break
        overlay._on_tick()


def test_hide_immediately_after_show_still_fades_out(overlay):
    """Regression: load finishing before the fade-in starts must not get stuck.

    Previously ``hide_overlay`` returned early while opacity was still 0.0,
    leaving the overlay shown and fading *in* over an already-loaded page.
    """
    overlay.show_overlay()
    assert overlay._opacity == 0.0  # fade-in has not advanced yet

    overlay.hide_overlay()
    assert overlay._fading_out is True  # must commit to fading out

    _run_fade_to_completion(overlay)
    assert not overlay.isVisible()


def test_hide_when_never_shown_is_noop(overlay):
    """hide_overlay on an unshown overlay should not arm a fade-out."""
    assert overlay.isHidden()
    overlay.hide_overlay()
    assert overlay._fading_out is False


def test_show_resets_fade_out_state(overlay):
    """A fresh show_overlay must clear a prior fade-out."""
    overlay.show_overlay()
    overlay.hide_overlay()
    assert overlay._fading_out is True

    overlay.show_overlay()
    assert overlay._fading_out is False
    assert overlay._opacity == 0.0


def test_base_icon_is_dpr_neutralized(overlay):
    """Regression: a HiDPI icon must be normalized to dpr 1.0 so the logo
    stays centered in the glow circle instead of the top-left quadrant.

    A pixmap with devicePixelRatio 2 reports width() in device pixels while
    drawPixmap() renders at the logical (half) size; the cached base pixmap
    must reset that ratio so the layout math matches what is drawn.
    """
    pixmap = QPixmap(512, 512)
    pixmap.fill()
    pixmap.setDevicePixelRatio(2.0)
    overlay.set_app_icon(QIcon(pixmap))

    base = overlay._load_base_icon()
    assert base is not None
    assert base.devicePixelRatio() == 1.0
