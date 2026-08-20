"""Native library browser — the API-driven replacement for the web view.

Fetches libraries, games and download providers from the Gameyfin server and
shows them as a cover grid with a detail page, so the desktop client only
renders what a game library actually needs. Enabled by ``GF_NATIVE_UI``.
"""

import logging

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from ..config import COVER_TILE_HEIGHT, COVER_TILE_WIDTH
from ..services.gameyfin_api import (DownloadProvider, Game, GameyfinApiClient,
                                     GameyfinApiError, GameyfinAuthError, Library)
from ..services.image_cache import ImageCache
from ..settings import SettingsManager
from ..utils import format_size
from ..workers import ApiCallWorker
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
        # image id -> grid item still waiting for its cover
        self._pending_covers: dict[int, QListWidgetItem] = {}

        self.image_cache.ready.connect(self._on_cover_ready)

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
        self.library_combo.currentIndexChanged.connect(lambda _: self._apply_filter())
        top_bar.addWidget(self.library_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.search_edit.setPlaceholderText("Search games…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _: self._apply_filter())
        top_bar.addWidget(self.search_edit, 1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_button.clicked.connect(self.refresh)
        top_bar.addWidget(self.refresh_button)
        grid_layout.addLayout(top_bar)

        self.status_label = QLabel("Not loaded yet.")
        self.status_label.setStyleSheet("font-size: 11px; color: palette(mid);")
        grid_layout.addWidget(self.status_label)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(10)
        self.grid.setIconSize(QSize(COVER_TILE_WIDTH, COVER_TILE_HEIGHT))
        self.grid.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.grid.itemActivated.connect(self._open_item)
        self.grid.itemDoubleClicked.connect(self._open_item)
        self.grid.verticalScrollBar().valueChanged.connect(lambda _: self._load_visible_covers())
        grid_layout.addWidget(self.grid, 1)

        self.stack.addWidget(grid_page)

        self.detail = GameDetailWidget(self.image_cache, self.settings, self)
        self.detail.back_requested.connect(self.show_grid)
        self.detail.download_requested.connect(self.download_requested.emit)
        self.stack.addWidget(self.detail)

        self._placeholder_icon = self._build_placeholder_icon()

    @staticmethod
    def _build_placeholder_icon() -> QIcon:
        """Return a neutral tile shown until a cover arrives (or when none exists)."""
        pixmap = QPixmap(COVER_TILE_WIDTH, COVER_TILE_HEIGHT)
        pixmap.fill(QColor(60, 60, 66))
        return QIcon(pixmap)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """(Re)load libraries, games and download providers.

        Calls that run inside the web view have to be made from the GUI thread —
        they only block the renderer, so the interface stays responsive. The direct
        HTTP fallback would block, so that one goes to a worker thread.
        """
        if self._worker is not None and self._worker.isRunning():
            # Queue it instead of dropping it: the request that arrives while a
            # fetch is winding down is usually the one made right after login.
            self._refresh_pending = True
            return

        self.status_label.setText("Loading library…")
        self.refresh_button.setEnabled(False)

        transport = getattr(self.api_client, "rpc_transport", None)
        if transport is not None and transport.available():
            self._refresh_in_page()
            return

        self._worker = ApiCallWorker(self._fetch_bundle)
        self._worker.result_ready.connect(self._on_bundle_loaded)
        self._worker.auth_required.connect(self.login_required.emit)
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
        """Fetch through the web view on this thread and populate the grid."""
        try:
            result = self._fetch_bundle()
        except GameyfinAuthError as e:
            logger.debug("Library fetch not authorized: %s", e)
            self.refresh_button.setEnabled(True)
            self.status_label.setText("Waiting for login…")
            self.login_required.emit()
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
            self.status_label.setText(error or "Could not load the library.")
            return

        self.libraries, self.games, self.providers = result  # type: ignore[misc]
        self.detail.set_providers(self.providers)
        self._populate_library_combo()
        self._apply_filter()
        self.library_loaded.emit()

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
        return sorted(games, key=lambda g: g.title.lower())

    def _apply_filter(self) -> None:
        """Rebuild the grid for the current filters."""
        games = self.visible_games()
        self._pending_covers.clear()
        self.grid.clear()

        for game in games:
            item = QListWidgetItem(game.title)
            item.setIcon(self._placeholder_icon)
            item.setData(GAME_ID_ROLE, game.id)
            if game.cover:
                item.setData(IMAGE_ID_ROLE, game.cover.id)
            item.setSizeHint(QSize(COVER_TILE_WIDTH + 16, COVER_TILE_HEIGHT + 44))
            item.setToolTip(self._tooltip_for(game))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.grid.addItem(item)

        total = len(self.games)
        self.status_label.setText(
            f"{len(games)} of {total} games" if total else "This server reports no games."
        )
        self._load_visible_covers()

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
        self.stack.setCurrentWidget(self.detail)
        self.detail.download_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_grid(self) -> None:
        """Return to the cover grid."""
        self.stack.setCurrentIndex(0)
        self.grid.setFocus(Qt.FocusReason.OtherFocusReason)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Load covers exposed by a resize."""
        super().resizeEvent(event)
        self._load_visible_covers()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Wait for an in-flight fetch so the thread does not outlive the widget."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        super().closeEvent(event)
