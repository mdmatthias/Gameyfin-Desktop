"""Native library browser — the API-driven replacement for the web view.

Fetches libraries, games and download providers from the Gameyfin server and
shows them as a cover grid with a detail page, so the desktop client only
renders what a game library actually needs. Enabled by ``GF_NATIVE_UI``.

When the server cannot be reached the browser goes offline: it shows only the
installed games, so they can still be launched. Their details are stored in
each game's prefix link and their artwork in the image cache whenever the
library loads, so the offline view looks the same as the online one.
"""

import logging
import math
import os
import signal
import sys

from PyQt6.QtCore import QProcess, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QIcon, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QStackedWidget,
                             QVBoxLayout, QWidget)

from ..config import (COVER_TILE_HEIGHT, COVER_TILE_WIDTH, LIBRARY_PAGE_SIZE,
                      STOP_GAME_GRACE_MS)
from ..services.gameyfin_api import (DownloadProvider, Game, GameyfinApiClient,
                                     GameyfinApiError, GameyfinAuthError,
                                     GameyfinConnectionError, Library)
from ..services.game_launcher import launch_script, stop_game
from ..services.image_cache import ImageCache
from ..services.installed_games import InstalledGame, InstalledGamesService
from ..settings import SettingsManager
from ..utils import format_size, muted_text_color, release_year
from ..workers import ApiCallWorker
from .cover_tile import CoverTileDelegate, tile_size_hint
from .game_detail import GameDetailWidget

logger = logging.getLogger(__name__)

GAME_ID_ROLE = Qt.ItemDataRole.UserRole
IMAGE_ID_ROLE = Qt.ItemDataRole.UserRole + 1

ALL_LIBRARIES = -1


class LibraryBrowserWidget(QWidget):
    """Cover grid plus detail page, backed by the Gameyfin server API."""

    download_requested = pyqtSignal(object, str)  # (Game, provider key)
    login_required = pyqtSignal()
    # Emitted when a fetch came back authorized — the session works
    library_loaded = pyqtSignal()
    # True when the server became unreachable, False once it answers again
    offline_changed = pyqtSignal(bool)
    # Refresh pressed while offline: check in the background whether the server is back
    reconnect_requested = pyqtSignal()
    # The user wants the Gameyfin web app instead of this native library
    web_view_requested = pyqtSignal()

    def __init__(self, api_client: GameyfinApiClient, image_cache: ImageCache,
                 settings: SettingsManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api_client = api_client
        self.image_cache = image_cache
        self.settings = settings

        self.games: list[Game] = []
        self.libraries: list[Library] = []
        self.providers: list[DownloadProvider] = []
        self._worker: ApiCallWorker | None = None
        # Set when a refresh is asked for while one is still in flight
        self._refresh_pending = False
        # True while an in-page fetch waits in its nested event loop
        self._in_page_refresh = False
        # image id -> grid item still waiting for its cover
        self._pending_covers: dict[int, QListWidgetItem] = {}
        # Gameyfin game id -> local install, refreshed from the prefixes on disk
        self.installed: dict[int, InstalledGame] = {}
        self._installed_service = InstalledGamesService(settings)
        # Launched games' process + loading dialog, kept alive while running
        self._launch_refs: list[tuple[object, object]] = []
        # Gameyfin game id -> launch script process of the games running now
        self._running: dict[int, QProcess] = {}
        # Set while the server is unreachable (see go_offline)
        self.offline = False

        # Client-side paging. The server returns every game in one call, so the
        # grid slices the filtered result set into pages of this size.
        self._page_size = self._initial_page_size()
        self._page = 0
        self._page_count = 0  # pages rendered last time; avoids rebuilding the dropdown

        self.image_cache.ready.connect(self._on_cover_ready)
        self.image_cache.failed.connect(self._on_cover_failed)

        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the grid page, detail page and the stack holding both."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(8, 8, 8, 8)
        grid_layout.setSpacing(8)

        top_bar = QHBoxLayout()
        self.library_combo = QComboBox()
        self.library_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.library_combo.addItem("All libraries", ALL_LIBRARIES)
        self.library_combo.currentIndexChanged.connect(lambda _: self._change_filter())
        top_bar.addWidget(self.library_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.search_edit.setPlaceholderText("Search games…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _: self._change_filter())
        top_bar.addWidget(self.search_edit, 1)

        self.installed_check = QCheckBox("Installed only")
        self.installed_check.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.installed_check.toggled.connect(lambda _: self._change_filter())
        # Only Linux installs create prefixes that can be linked to a game
        self.installed_check.setVisible(sys.platform != "win32")
        top_bar.addWidget(self.installed_check)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_button.clicked.connect(self.refresh)
        top_bar.addWidget(self.refresh_button)

        self.web_view_button = QPushButton("Web view")
        self.web_view_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.web_view_button.setToolTip("Switch to the Gameyfin web app")
        self.web_view_button.clicked.connect(self.web_view_requested.emit)
        top_bar.addWidget(self.web_view_button)

        self.prev_button = QPushButton("‹ Prev")
        self.prev_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.prev_button.setToolTip("Previous page")
        self.prev_button.clicked.connect(self._previous_page)
        top_bar.addWidget(self.prev_button)

        self.page_label = QLabel("1/1")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setStyleSheet(f"font-size: 11px; color: {muted_text_color(self)};")
        # Reserve room for a two-digit page number so the neighbouring
        # buttons don't shift when going e.g. from page 9 to 10.
        indicator_font = QFont()
        indicator_font.setPixelSize(11)
        widest = QFontMetrics(indicator_font).horizontalAdvance("99/99")
        self.page_label.setMinimumWidth(widest + 8)
        top_bar.addWidget(self.page_label)

        self.next_button = QPushButton("Next ›")
        self.next_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.next_button.setToolTip("Next page")
        self.next_button.clicked.connect(self._next_page)
        top_bar.addWidget(self.next_button)
        grid_layout.addLayout(top_bar)

        self.status_label = QLabel("Not loaded yet.")
        self.status_label.setStyleSheet(f"font-size: 11px; color: {muted_text_color(self)};")
        grid_layout.addWidget(self.status_label)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(6)
        self.grid.setIconSize(QSize(COVER_TILE_WIDTH, COVER_TILE_HEIGHT))
        self.grid.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # The tiles are painted by the delegate, so the view only has to hand
        # it a flat surface — no frame, no style-drawn selection background.
        self.grid.setFrameShape(QListWidget.Shape.NoFrame)
        self.grid.setItemDelegate(CoverTileDelegate(self.grid))
        self.grid.setMouseTracking(True)  # so tiles can react to hover
        self.grid.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.grid.itemActivated.connect(self._open_item)
        self.grid.itemClicked.connect(self._open_item)
        self.grid.verticalScrollBar().valueChanged.connect(lambda _: self._load_visible_covers())
        grid_layout.addWidget(self.grid, 1)

        self.stack.addWidget(grid_page)

        self.detail = GameDetailWidget(self.image_cache, self.settings, self)
        self.detail.back_requested.connect(self.show_grid)
        self.detail.download_requested.connect(self.download_requested.emit)
        self.detail.play_requested.connect(self._play)
        self.detail.stop_requested.connect(self._stop_shown_game)
        self.detail.script_selected.connect(self._remember_script)
        self.stack.addWidget(self.detail)

    # ------------------------------------------------------------------
    # Paging
    # ------------------------------------------------------------------

    def _initial_page_size(self) -> int:
        """Read the configured page size, falling back to the default."""
        try:
            value = int(self.settings.get("GF_LIBRARY_PAGE_SIZE", LIBRARY_PAGE_SIZE))
        except (TypeError, ValueError):
            value = LIBRARY_PAGE_SIZE
        return max(1, value)

    @property
    def page_size(self) -> int:
        """Games shown per page in the grid."""
        return self._page_size

    def set_page_size(self, size: int) -> None:
        """Change the page size and re-render the grid from page one."""
        size = max(1, int(size))
        if size == self._page_size:
            return
        self._page_size = size
        self._page = 0
        self._apply_filter()

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Pick up the theme's resolved colours once the widget is polished."""
        super().showEvent(event)
        self.refresh_theme_colors()
        # Prefixes may have been installed or deleted while we were hidden
        if self.games:
            self.refresh_installed()

    def refresh_theme_colors(self) -> None:
        """Re-apply palette-derived colours after the theme changed."""
        muted = muted_text_color(self)
        self.page_label.setStyleSheet(f"font-size: 11px; color: {muted};")
        self.status_label.setStyleSheet(f"font-size: 11px; color: {muted};")
        self.detail.refresh_theme_colors()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """(Re)load libraries, games and download providers.

        Calls that run inside the web view have to be made from the GUI thread —
        they only block the renderer, so the interface stays responsive. The direct
        HTTP fallback would block, so that one goes to a worker thread.
        """
        if self._in_page_refresh or (self._worker is not None and self._worker.isRunning()):
            # Queue it instead of dropping it: the request that arrives while a
            # fetch is winding down is usually the one made right after login.
            self._refresh_pending = True
            return

        if self.offline:
            # A fetch here would block the GUI thread until it times out
            self.status_label.setText("Offline: checking whether the server is back…")
            self.reconnect_requested.emit()
            return

        self.status_label.setText("Loading library…")
        self.refresh_button.setEnabled(False)

        transport = getattr(self.api_client, "rpc_transport", None)
        if transport is not None and transport.available():
            self._refresh_in_page()
            return

        self._worker = ApiCallWorker(self._fetch_bundle)
        self._worker.result_ready.connect(self._on_bundle_loaded)
        self._worker.auth_required.connect(self.set_online)
        self._worker.auth_required.connect(self.login_required.emit)
        self._worker.unreachable.connect(self.go_offline)
        self._worker.finished.connect(self._release_worker)
        self._worker.start()

    def _release_worker(self) -> None:
        """Drop the finished fetch thread once it has left ``run()``.

        The reference is only cleared here — releasing it from the result handler
        would destroy a QThread that is technically still running.
        """
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.wait()
            worker.deleteLater()

        if self._refresh_pending:
            self._refresh_pending = False
            # Deferred so this returns before the next fetch starts
            QTimer.singleShot(0, self.refresh)

    def _refresh_in_page(self) -> None:
        """Fetch through the web view on this thread and populate the grid.

        The fetch waits in a nested event loop, so timers and signals can ask
        for another refresh meanwhile. Those are queued rather than started
        inside it: a nested fetch would finish first, and the outer, older one
        would then overwrite its result.
        """
        self._in_page_refresh = True
        try:
            self._do_refresh_in_page()
        finally:
            self._in_page_refresh = False
        if self._refresh_pending:
            self._refresh_pending = False
            QTimer.singleShot(0, self.refresh)

    def _do_refresh_in_page(self) -> None:
        try:
            result = self._fetch_bundle()
        except GameyfinAuthError as e:
            logger.debug("Library fetch not authorized: %s", e)
            self.refresh_button.setEnabled(True)
            self.set_online()
            self.status_label.setText("Waiting for login…")
            self.login_required.emit()
            return
        except GameyfinConnectionError as e:
            self.refresh_button.setEnabled(True)
            self.go_offline(str(e))
            return
        except GameyfinApiError as e:
            self.refresh_button.setEnabled(True)
            self.status_label.setText(str(e))
            return

        self._on_bundle_loaded(result, "")

    def _fetch_bundle(self) -> tuple[list[Library], list[Game], list[DownloadProvider]]:
        """Fetch everything the grid needs in one worker run."""
        libraries = self.api_client.get_libraries()
        games = self.api_client.get_games()
        providers = self.api_client.get_download_providers()
        return libraries, games, providers

    def _on_bundle_loaded(self, result: object, error: str) -> None:
        """Populate the grid from a completed fetch, or report the failure."""
        self.refresh_button.setEnabled(True)

        if error or result is None:
            if not self.offline:  # go_offline already set the status line
                self.status_label.setText(error or "Could not load the library.")
            return

        self.libraries, self.games, self.providers = result  # type: ignore[misc]
        self.set_online()
        self.detail.set_providers(self.providers)
        self._populate_library_combo()
        self._scan_installed()
        self._remember_installed_games()
        self._apply_filter()
        self.library_loaded.emit()

    # ------------------------------------------------------------------
    # Offline mode
    # ------------------------------------------------------------------

    def go_offline(self, reason: str = "") -> None:
        """Show the installed games without the server.

        The games come from the library loaded this session, else from the
        details stored with each installed game. A game installed before those
        were stored is listed by the title remembered in its prefix link.
        """
        if self.offline:
            # Still offline: keep the grid (and the gamepad focus) as it is
            self._update_status(len(self.visible_games()), 0, 0)
            return

        logger.info("Server unreachable, showing installed games offline: %s", reason)
        self._scan_installed()
        self._add_unknown_installed_games()

        self.offline = True
        self.image_cache.offline = True
        self.detail.set_offline(True)
        self.installed_check.setVisible(False)
        self.web_view_button.setEnabled(False)
        self.web_view_button.setToolTip("The web app needs the server, which can't be reached")
        self._page = 0
        self._apply_filter()
        self.offline_changed.emit(True)

    def _add_unknown_installed_games(self) -> None:
        """Add the installed games the loaded library doesn't know, from their stored details."""
        known = {game.id for game in self.games}
        self.games = self.games + [
            self._stored_game(info)
            for info in self.installed.values() if info.game_id not in known
        ]

    @staticmethod
    def _stored_game(info: InstalledGame) -> Game:
        """Return the game stored with an install, or a bare entry when there is none."""
        if info.details:
            try:
                game = Game.from_dict(info.details)
                if game.id == info.game_id:
                    return game
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning("Stored details of '%s' are unusable: %s", info.game_name, e)
        return Game(id=info.game_id, title=info.title, library_id=0)

    def set_online(self) -> None:
        """Leave offline mode: the server answered again."""
        if not self.offline:
            return
        logger.info("Server reachable again, leaving offline mode")
        self.offline = False
        self.image_cache.offline = False
        self.detail.set_offline(False)
        self.installed_check.setVisible(sys.platform != "win32")
        self.web_view_button.setEnabled(True)
        self.web_view_button.setToolTip("Switch to the Gameyfin web app")
        self._apply_filter()
        self.offline_changed.emit(False)

    def _populate_library_combo(self) -> None:
        """Rebuild the library selector, keeping the current selection if possible."""
        previous = self.library_combo.currentData()
        self.library_combo.blockSignals(True)
        self.library_combo.clear()
        self.library_combo.addItem("All libraries", ALL_LIBRARIES)
        for library in sorted(self.libraries, key=lambda lib: lib.name.lower()):
            self.library_combo.addItem(library.name, library.id)
        index = self.library_combo.findData(previous)
        self.library_combo.setCurrentIndex(index if index >= 0 else 0)
        self.library_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Installed games
    # ------------------------------------------------------------------

    def _scan_installed(self) -> None:
        """Re-read which games are installed locally."""
        self.installed = self._installed_service.scan(self.games)

    def _remember_installed_games(self) -> None:
        """Store each installed game's details and artwork, for offline use.

        Details are only rewritten when the server's data changed. Artwork
        already on disk is not fetched again.
        """
        for info in self.installed.values():
            game = self.game_by_id(info.game_id)
            if game is None:
                continue
            details = game.to_dict()
            if info.details != details:
                try:
                    self._installed_service.save_details(info.game_name, details)
                    info.details, info.title = details, game.title
                except OSError as e:
                    logger.error("Could not store the details of '%s': %s", info.game_name, e)
            for image in game.artwork():
                self.image_cache.request(image)

    def refresh_installed(self) -> None:
        """Rescan the installed games and update the grid and detail page."""
        before = set(self.installed)
        self._scan_installed()
        if self.offline:
            # A game installed while offline (from a local archive) needs an entry
            self._add_unknown_installed_games()
        else:
            self._remember_installed_games()
        if ((self.offline or self.installed_check.isChecked())
                and set(self.installed) != before):
            # The filtered result set changed: rebuild the page
            self._apply_filter()
        else:
            # Only the badges change — keep the grid (and its selection) as is
            for row in range(self.grid.count()):
                item = self.grid.item(row)
                item.setData(CoverTileDelegate.INSTALLED_ROLE,
                             item.data(GAME_ID_ROLE) in self.installed)
        game = self.detail.game
        if self.stack.currentWidget() is self.detail and game is not None:
            self._show_installed(game.id)

    def _installed_for_script(self, script_path: str) -> InstalledGame | None:
        for info in self.installed.values():
            if script_path in info.scripts:
                return info
        return None

    def _remember_script(self, script_path: str) -> None:
        """Persist the launch script picked for an installed game."""
        info = self._installed_for_script(script_path)
        if info is None:
            return
        info.last_script = os.path.basename(script_path)
        self._installed_service.set_last_script(info.game_name, script_path)

    def _show_installed(self, game_id: int) -> None:
        """Update the detail page's Play/Stop row for *game_id*."""
        self.detail.set_installed(self.installed.get(game_id))
        self.detail.set_running(game_id in self._running)

    def _play(self, script_path: str) -> None:
        """Launch an installed game's script."""
        info = self._installed_for_script(script_path)
        if info is not None and info.game_id in self._running:
            return  # Already running: don't start a second copy
        self._remember_script(script_path)
        try:
            process, dialog = launch_script(script_path, self)
        except OSError as e:
            logger.error("Failed to launch script %s: %s", script_path, e)
            QMessageBox.critical(self, "Launch Error", f"Failed to launch: {e}")
            return
        refs = (process, dialog)
        self._launch_refs.append(refs)
        process.finished.connect(dialog.close)
        process.finished.connect(lambda *_: self._launch_refs.remove(refs)
                                 if refs in self._launch_refs else None)
        if info is not None:
            game_id = info.game_id
            self._running[game_id] = process
            process.finished.connect(lambda *_: self._on_game_exited(game_id, process))
            if self.detail.game is not None and self.detail.game.id == game_id:
                self.detail.set_running(True)

    def _on_game_exited(self, game_id: int, process: QProcess) -> None:
        """Forget a game whose launch script finished and reset its Play button."""
        if self._running.get(game_id) is not process:
            return
        del self._running[game_id]
        if self.detail.game is not None and self.detail.game.id == game_id:
            self.detail.set_running(False)

    def _stop_shown_game(self) -> None:
        """Stop the game shown on the detail page."""
        if self.detail.game is not None:
            self.stop(self.detail.game.id)

    def stop(self, game_id: int) -> None:
        """Ask a running game to quit, and kill whatever is left after a grace period.

        Both passes cover the launch script's process tree and every process
        running in the game's prefix, so a launcher that handed off to the game
        (or a lingering wineserver) is stopped too.
        """
        process = self._running.get(game_id)
        if process is None:
            return
        info = self.installed.get(game_id)
        prefix = info.prefix_path if info else None
        if sys.platform == "win32":
            process.kill()
            return
        stop_game(process.processId() or None, prefix, signal.SIGTERM)

        def _kill_leftovers() -> None:
            # Only follow the script's tree while it is still ours to signal
            alive = self._running.get(game_id) is process
            stop_game(process.processId() if alive else None, prefix, signal.SIGKILL)

        QTimer.singleShot(STOP_GAME_GRACE_MS, _kill_leftovers)

    # ------------------------------------------------------------------
    # Filtering / grid
    # ------------------------------------------------------------------

    def visible_games(self) -> list[Game]:
        """Return the games matching the current library and search filters."""
        library_id = self.library_combo.currentData()
        needle = self.search_edit.text().strip().lower()

        games = self.games
        if library_id is not None and library_id != ALL_LIBRARIES:
            games = [g for g in games if g.library_id == library_id]
        if needle:
            games = [g for g in games if needle in g.title.lower()]
        if self.offline or self.installed_check.isChecked():
            games = [g for g in games if g.id in self.installed]
        return sorted(games, key=lambda g: g.title.lower())

    def _change_filter(self) -> None:
        """A library or search change: jump back to the first page and re-render."""
        self._page = 0
        self._apply_filter()

    def _previous_page(self) -> None:
        """Step back one page."""
        if self._page > 0:
            self._page -= 1
            self._apply_filter()

    def _next_page(self) -> None:
        """Step forward one page."""
        if self._page < self._page_count - 1:
            self._page += 1
            self._apply_filter()

    def _apply_filter(self) -> None:
        """Rebuild the grid for the current filters and page."""
        games = self.visible_games()
        total_pages = max(1, math.ceil(len(games) / self._page_size))
        # Keep the current page within range after the result set changed size.
        self._page = min(max(self._page, 0), total_pages - 1)

        start = self._page * self._page_size
        page_games = games[start:start + self._page_size]

        self._pending_covers.clear()
        self.grid.clear()

        for game in page_games:
            item = QListWidgetItem(game.title)
            # No placeholder icon: until the cover arrives the delegate paints
            # a palette-derived block, which follows the active theme.
            item.setData(GAME_ID_ROLE, game.id)
            if game.cover:
                item.setData(IMAGE_ID_ROLE, game.cover.id)
            item.setData(CoverTileDelegate.META_ROLE, self._meta_for(game))
            item.setData(CoverTileDelegate.INSTALLED_ROLE, game.id in self.installed)
            item.setSizeHint(tile_size_hint(self.grid.font()))
            item.setToolTip(self._tooltip_for(game))
            self.grid.addItem(item)

        self._update_status(len(games), start, len(page_games))
        self._update_paging_controls(total_pages)
        self._load_visible_covers()

    def _update_status(self, filtered: int, start: int, shown: int) -> None:
        """Set the status line to describe what the current page holds."""
        if self.offline:
            self.status_label.setText(
                "Offline: can't reach the server. Showing installed games; "
                "retrying in the background."
                if filtered else
                "Offline: can't reach the server, and no installed games match."
            )
        elif not self.games:
            self.status_label.setText("This server reports no games.")
        elif filtered == 0 and self.installed_check.isChecked():
            self.status_label.setText("No installed games match.")
        elif filtered == 0:
            self.status_label.setText("No games match your search.")
        else:
            self.status_label.setText(
                f"Showing {start + 1}–{start + shown} of {filtered} games"
            )

    def _update_paging_controls(self, total_pages: int) -> None:
        """Sync the prev/next buttons and page indicator with the current page."""
        self._page_count = total_pages
        self.page_label.setText(f"{self._page + 1}/{total_pages}")
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < total_pages - 1)

    @staticmethod
    def _meta_for(game: Game) -> str:
        """Return the muted line under a tile's title (release year, size)."""
        parts = []
        year = release_year(game.release)
        if year:
            parts.append(year)
        if game.file_size:
            parts.append(format_size(game.file_size))
        return " · ".join(parts)

    @staticmethod
    def _tooltip_for(game: Game) -> str:
        """Return the hover text for a grid tile."""
        parts = [game.title]
        if game.release:
            parts.append(str(game.release))
        if game.file_size:
            parts.append(format_size(game.file_size))
        return " · ".join(parts)

    def game_by_id(self, game_id: int) -> Game | None:
        """Return the loaded game with *game_id*, or None."""
        for game in self.games:
            if game.id == game_id:
                return game
        return None

    def _load_visible_covers(self) -> None:
        """Request covers for the tiles currently on screen."""
        viewport = self.grid.viewport().rect()
        covers = {g.cover.id: g.cover for g in self.games if g.cover}

        for row in range(self.grid.count()):
            item = self.grid.item(row)
            image_id = item.data(IMAGE_ID_ROLE)
            if image_id is None or image_id in self._pending_covers:
                continue
            if not viewport.intersects(self.grid.visualItemRect(item)):
                continue
            image = covers.get(image_id)
            if image is None:
                continue
            data = self.image_cache.request(image)
            if data is not None:
                self._apply_cover(item, data)
            else:
                self._pending_covers[image_id] = item

    def _on_cover_ready(self, image_id: int, data: bytes) -> None:
        """Apply a background-fetched cover to its grid tile."""
        item = self._pending_covers.pop(image_id, None)
        if item is None:
            return
        self._apply_cover(item, data)

    def _on_cover_failed(self, image_id: int, _message: str) -> None:
        """Drop a failed fetch from the pending set so it can be retried later."""
        self._pending_covers.pop(image_id, None)

    @staticmethod
    def _apply_cover(item: QListWidgetItem, data: bytes) -> None:
        """Scale *data* into the tile icon."""
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        item.setIcon(QIcon(pixmap.scaled(
            COVER_TILE_WIDTH, COVER_TILE_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _open_item(self, item: QListWidgetItem) -> None:
        """Open the detail page for the activated tile."""
        game = self.game_by_id(item.data(GAME_ID_ROLE))
        if game is None:
            return
        self.detail.show_game(game)
        self._show_installed(game.id)
        self.stack.setCurrentWidget(self.detail)
        target = (self.detail.play_button if self.detail.play_button.isEnabled()
                  and self.detail.installed is not None else self.detail.download_button)
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_grid(self) -> None:
        """Return to the cover grid."""
        self.stack.setCurrentIndex(0)
        self.grid.setFocus(Qt.FocusReason.OtherFocusReason)

    def gamepad_back(self) -> bool:
        """Handle the gamepad's B button: leave the detail page for the grid."""
        if self.stack.currentWidget() is not self.detail:
            return False
        self.show_grid()
        return True

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Load covers exposed by a resize."""
        super().resizeEvent(event)
        self._load_visible_covers()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Wait for an in-flight fetch so the thread does not outlive the widget."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        super().closeEvent(event)
