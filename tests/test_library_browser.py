"""Tests for the native library UI: image cache, cover grid and detail view."""

from unittest.mock import MagicMock

import pytest

from gameyfin_frontend.services.gameyfin_api import (DownloadProvider, Game,
                                                     GameImage,
                                                     GameyfinApiError, Library)
from gameyfin_frontend.services.image_cache import ImageCache
from gameyfin_frontend.utils import format_size
from gameyfin_frontend.widgets.game_detail import GameDetailWidget
from gameyfin_frontend.widgets.library_browser import (ALL_LIBRARIES,
                                                       GAME_ID_ROLE,
                                                       LibraryBrowserWidget)

@pytest.fixture()
def png_bytes(qtbot):
    """Return the bytes of a small real PNG (encoded by Qt itself)."""
    from PyQt6.QtCore import QBuffer
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(4, 4)
    pixmap.fill()
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


@pytest.fixture()
def cache_settings(tmp_path):
    """Settings stub whose config dir is a temp directory."""
    settings = MagicMock()
    settings.get_config_dir.return_value = str(tmp_path)
    return settings


@pytest.fixture()
def sample_games():
    """Two games in two libraries, one with a cover."""
    return [
        Game(id=1, title="Alpha", library_id=1, file_size=1024,
             cover=GameImage(id=11, type="COVER")),
        Game(id=2, title="Beta", library_id=2, summary="Second game",
             release="2001-01-01", user_rating=80, critic_rating=70,
             platforms=["WINDOWS"], genres=["Shooter"], developers=["Dev"],
             publishers=["Pub"], file_size=2048,
             header=GameImage(id=22, type="HEADER"),
             images=[GameImage(id=23, type="SCREENSHOT")]),
    ]


@pytest.fixture()
def sample_libraries():
    return [Library(id=1, name="First"), Library(id=2, name="Second")]


@pytest.fixture()
def sample_providers():
    return [DownloadProvider(key="fs", name="Filesystem", priority=5)]


@pytest.fixture()
def mock_api(sample_libraries, sample_games, sample_providers):
    """API client stub returning the sample bundle."""
    client = MagicMock()
    client.get_libraries.return_value = sample_libraries
    client.get_games.return_value = sample_games
    client.get_download_providers.return_value = sample_providers
    client.download_url.side_effect = lambda gid, key: f"http://srv/download/{gid}?provider={key}"
    return client


@pytest.fixture()
def mock_cache():
    """Image cache stub that never resolves artwork."""
    cache = MagicMock()
    cache.request.return_value = None
    return cache


class TestImageCache:
    def test_cached_bytes_reads_existing_file(self, cache_settings, tmp_path):
        cache = ImageCache(MagicMock(), cache_settings)
        image = GameImage(id=1, type="COVER")
        with open(cache.cache_path(image), "wb") as f:
            f.write(b"DATA")

        assert cache.cached_bytes(image) == b"DATA"

    def test_cached_bytes_returns_none_when_absent(self, cache_settings):
        cache = ImageCache(MagicMock(), cache_settings)

        assert cache.cached_bytes(GameImage(id=99, type="COVER")) is None

    def test_request_returns_cached_bytes_without_fetching(self, cache_settings):
        client = MagicMock()
        cache = ImageCache(client, cache_settings)
        image = GameImage(id=2, type="COVER")
        with open(cache.cache_path(image), "wb") as f:
            f.write(b"CACHED")

        assert cache.request(image) == b"CACHED"
        client.fetch_image.assert_not_called()

    def test_request_fetches_and_caches_in_background(self, qtbot, cache_settings):
        client = MagicMock()
        client.fetch_image.return_value = b"FETCHED"
        cache = ImageCache(client, cache_settings)
        image = GameImage(id=3, type="COVER")

        with qtbot.waitSignal(cache.ready, timeout=5000) as blocker:
            assert cache.request(image) is None

        assert blocker.args == [3, b"FETCHED"]
        assert cache.cached_bytes(image) == b"FETCHED"

    def test_failed_fetch_emits_failed(self, qtbot, cache_settings):
        client = MagicMock()
        client.fetch_image.side_effect = GameyfinApiError("boom")
        cache = ImageCache(client, cache_settings)

        with qtbot.waitSignal(cache.failed, timeout=5000) as blocker:
            cache.request(GameImage(id=4, type="COVER"))

        assert blocker.args[0] == 4

    def test_duplicate_request_is_coalesced(self, qtbot, cache_settings):
        client = MagicMock()
        client.fetch_image.return_value = b"ONE"
        cache = ImageCache(client, cache_settings)
        image = GameImage(id=5, type="COVER")

        with qtbot.waitSignal(cache.ready, timeout=5000):
            cache.request(image)
            cache.request(image)

        assert client.fetch_image.call_count == 1

    def test_clear_removes_cached_files(self, cache_settings):
        cache = ImageCache(MagicMock(), cache_settings)
        image = GameImage(id=6, type="COVER")
        with open(cache.cache_path(image), "wb") as f:
            f.write(b"X")

        cache.clear()

        assert cache.cached_bytes(image) is None


class TestLibraryBrowser:
    @pytest.fixture()
    def browser(self, qtbot, mock_api, mock_cache, fresh_settings):
        widget = LibraryBrowserWidget(mock_api, mock_cache, fresh_settings)
        qtbot.addWidget(widget)
        return widget

    def test_refresh_populates_grid_and_libraries(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)

        assert [browser.grid.item(i).text() for i in range(2)] == ["Alpha", "Beta"]
        # "All libraries" plus the two server libraries
        assert browser.library_combo.count() == 3
        assert "Showing 1–2 of 2 games" in browser.status_label.text()

    def test_refresh_reports_errors(self, qtbot, browser, mock_api):
        mock_api.get_libraries.side_effect = GameyfinApiError("server down")

        browser.refresh()
        qtbot.waitUntil(lambda: "server down" in browser.status_label.text(), timeout=5000)

        assert browser.grid.count() == 0

    def test_auth_failure_emits_login_required(self, qtbot, browser, mock_api):
        from gameyfin_frontend.services.gameyfin_api import GameyfinAuthError
        mock_api.get_libraries.side_effect = GameyfinAuthError("nope")

        with qtbot.waitSignal(browser.login_required, timeout=5000):
            browser.refresh()

    def test_library_filter(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)

        browser.library_combo.setCurrentIndex(browser.library_combo.findData(2))

        assert [g.title for g in browser.visible_games()] == ["Beta"]
        assert browser.grid.count() == 1

    def test_search_filter_is_case_insensitive(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)

        browser.search_edit.setText("ALP")

        assert [g.title for g in browser.visible_games()] == ["Alpha"]

    def test_all_libraries_selection_shows_everything(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)

        browser.library_combo.setCurrentIndex(browser.library_combo.findData(ALL_LIBRARIES))

        assert len(browser.visible_games()) == 2

    def test_opening_a_tile_shows_the_detail_page(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)

        browser._open_item(browser.grid.item(1))

        assert browser.stack.currentWidget() is browser.detail
        assert browser.detail.game.title == "Beta"

        browser.show_grid()
        assert browser.stack.currentIndex() == 0

    def test_download_request_is_forwarded_with_provider(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)
        browser._open_item(browser.grid.item(0))

        with qtbot.waitSignal(browser.download_requested, timeout=1000) as blocker:
            browser.detail.download_button.click()

        game, provider_key = blocker.args
        assert game.id == 1
        assert provider_key == "fs"

    def test_game_by_id(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: bool(browser.games), timeout=5000)

        assert browser.game_by_id(2).title == "Beta"
        assert browser.game_by_id(999) is None

    def test_refresh_during_a_fetch_is_queued_not_dropped(self, qtbot, browser, mock_api):
        """The probe issued right after login must not be swallowed."""
        browser.refresh()
        browser._refresh_pending = False
        browser._worker = MagicMock()
        browser._worker.isRunning.return_value = True

        browser.refresh()

        assert browser._refresh_pending
        # Releasing the worker runs the queued refresh
        browser._worker = None
        browser._release_worker()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)
        assert not browser._refresh_pending

    def test_grid_items_carry_the_game_id(self, qtbot, browser):
        browser.refresh()
        qtbot.waitUntil(lambda: browser.grid.count() == 2, timeout=5000)

        assert browser.grid.item(0).data(GAME_ID_ROLE) == 1


class TestLibraryBrowserPaging:
    @pytest.fixture()
    def many_games(self):
        """23 games so a page size of 10 spans two pages."""
        return [Game(id=i, title=f"Game {i:02d}", library_id=1, file_size=1024)
                for i in range(1, 24)]

    @pytest.fixture()
    def paging_browser(self, qtbot, mock_cache, fresh_settings, many_games):
        client = MagicMock()
        client.get_libraries.return_value = [Library(id=1, name="First")]
        client.get_games.return_value = many_games
        client.get_download_providers.return_value = []
        widget = LibraryBrowserWidget(client, mock_cache, fresh_settings)
        qtbot.addWidget(widget)
        widget.set_page_size(10)
        return widget

    def test_first_page_renders_only_a_page_of_tiles(self, qtbot, paging_browser):
        paging_browser.refresh()
        qtbot.waitUntil(lambda: paging_browser.grid.count() > 0, timeout=5000)

        assert paging_browser.grid.count() == 10
        assert "Showing 1–10 of 23 games" in paging_browser.status_label.text()
        assert paging_browser.page_label.text() == "1/3"  # 23 games / 10 per page

    def test_next_and_previous_page_step_through(self, qtbot, paging_browser):
        paging_browser.refresh()
        qtbot.waitUntil(lambda: paging_browser.grid.count() > 0, timeout=5000)

        assert not paging_browser.prev_button.isEnabled()
        assert paging_browser.next_button.isEnabled()
        assert paging_browser.page_label.text() == "1/3"

        paging_browser.next_button.click()
        assert paging_browser.grid.count() == 10
        assert "Showing 11–20 of 23 games" in paging_browser.status_label.text()
        assert paging_browser.page_label.text() == "2/3"
        assert paging_browser.prev_button.isEnabled()

        paging_browser.next_button.click()
        assert paging_browser.grid.count() == 3
        assert "Showing 21–23 of 23 games" in paging_browser.status_label.text()
        assert paging_browser.page_label.text() == "3/3"
        assert not paging_browser.next_button.isEnabled()

        paging_browser.prev_button.click()
        assert "Showing 11–20 of 23 games" in paging_browser.status_label.text()
        assert paging_browser.page_label.text() == "2/3"

    def test_filter_change_resets_to_first_page(self, qtbot, paging_browser):
        paging_browser.refresh()
        qtbot.waitUntil(lambda: paging_browser.grid.count() > 0, timeout=5000)

        paging_browser.next_button.click()
        assert paging_browser._page == 1

        # A search narrows the result set and snaps back to page one
        paging_browser.search_edit.setText("Game 01")
        assert paging_browser._page == 0
        assert paging_browser.grid.count() == 1

    def test_set_page_size_repages_from_the_start(self, qtbot, paging_browser):
        paging_browser.refresh()
        qtbot.waitUntil(lambda: paging_browser.grid.count() > 0, timeout=5000)

        paging_browser.next_button.click()
        assert paging_browser._page == 1

        paging_browser.set_page_size(5)

        assert paging_browser.page_size == 5
        assert paging_browser._page == 0
        assert paging_browser.grid.count() == 5
        assert paging_browser.page_label.text() == "1/5"  # 23 / 5 -> 5 pages

    def test_set_page_size_ignores_noop_change(self, qtbot, paging_browser):
        paging_browser.refresh()
        qtbot.waitUntil(lambda: paging_browser.grid.count() > 0, timeout=5000)

        before = paging_browser.status_label.text()
        paging_browser.set_page_size(10)  # same as current

        assert paging_browser.status_label.text() == before
        assert paging_browser._page == 0

    def test_set_page_size_clamps_below_one(self, qtbot, paging_browser):
        paging_browser.set_page_size(0)

        assert paging_browser.page_size == 1

    def test_empty_library_reports_no_games(self, qtbot, fresh_settings, mock_cache):
        client = MagicMock()
        client.get_libraries.return_value = []
        client.get_games.return_value = []
        client.get_download_providers.return_value = []
        widget = LibraryBrowserWidget(client, mock_cache, fresh_settings)
        qtbot.addWidget(widget)

        widget.refresh()
        qtbot.waitUntil(lambda: widget.status_label.text() != "", timeout=5000)

        assert widget.grid.count() == 0
        assert widget.page_label.text() == "1/1"
        assert not widget.next_button.isEnabled()
        assert not widget.prev_button.isEnabled()

    def test_page_label_reserves_width_for_two_digit_pages(self, paging_browser):
        """The indicator keeps a stable width so the buttons don't jump 9 -> 10."""
        from PyQt6.QtGui import QFont, QFontMetrics

        font = QFont()
        font.setPixelSize(11)
        needed = QFontMetrics(font).horizontalAdvance("99/99")

        assert paging_browser.page_label.minimumWidth() >= needed


class TestGameDetail:
    @pytest.fixture()
    def detail(self, qtbot, mock_cache, fresh_settings):
        widget = GameDetailWidget(mock_cache, fresh_settings)
        qtbot.addWidget(widget)
        return widget

    def test_shows_all_metadata_fields(self, detail, sample_games):
        detail.show_game(sample_games[1])

        assert detail.title_label.text() == "Beta"
        assert detail.summary_label.text() == "Second game"
        assert "2001-01-01" in detail.subtitle_label.text()
        assert "80" in detail.rating_label.text()
        assert "70" in detail.rating_label.text()
        assert detail.detail_labels["Genres"].text() == "Shooter"
        assert detail.detail_labels["Developers"].text() == "Dev"
        assert detail.detail_labels["Publishers"].text() == "Pub"
        assert detail.detail_labels["Platforms"].text() == "WINDOWS"
        assert detail.detail_labels["Size"].text() == format_size(2048)

    def test_missing_fields_render_placeholders(self, detail, sample_games):
        detail.show_game(sample_games[0])

        assert detail.summary_label.text() == "No description available."
        assert detail.detail_labels["Genres"].text() == "—"
        assert detail.rating_label.text() == ""

    def test_screenshot_strip_only_shown_when_present(self, detail, sample_games):
        detail.show_game(sample_games[0])
        assert detail.screenshot_scroll.isHidden()

        detail.show_game(sample_games[1])
        assert not detail.screenshot_scroll.isHidden()

    def test_provider_picker_hidden_for_single_provider(self, detail, sample_providers):
        detail.set_providers(sample_providers)

        assert detail.provider_combo.isHidden()
        assert detail.selected_provider_key() == "fs"
        assert detail.download_button.isEnabled()

    def test_provider_picker_shown_for_multiple_providers(self, detail):
        detail.set_providers([
            DownloadProvider(key="a", name="A", priority=2),
            DownloadProvider(key="b", name="B", priority=1),
        ])

        assert not detail.provider_combo.isHidden()
        detail.provider_combo.setCurrentIndex(1)
        assert detail.selected_provider_key() == "b"

    def test_download_disabled_without_providers(self, detail, sample_games):
        detail.set_providers([])
        detail.show_game(sample_games[0])

        assert not detail.download_button.isEnabled()
        assert detail.selected_provider_key() == ""

    def test_no_download_signal_without_provider(self, qtbot, detail, sample_games):
        detail.set_providers([])
        detail.show_game(sample_games[0])
        emitted = []
        detail.download_requested.connect(lambda *a: emitted.append(a))

        detail._emit_download()

        assert emitted == []

    def test_background_image_is_applied_to_its_label(self, detail, sample_games, png_bytes):
        detail.show_game(sample_games[1])
        assert 22 in detail._pending_images

        detail._on_image_ready(22, png_bytes)

        assert 22 not in detail._pending_images
        assert not detail.header_label.pixmap().isNull()

    def test_back_button_emits_back_requested(self, qtbot, detail):
        with qtbot.waitSignal(detail.back_requested, timeout=1000):
            detail.back_button.click()
