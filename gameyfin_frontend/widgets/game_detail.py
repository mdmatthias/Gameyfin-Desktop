"""Native detail view for a single game.

Shows everything the server reports for a game — header art, cover, summary,
release date, ratings, genres/themes, platforms, developers/publishers, file
size and screenshots — plus the download button. Nothing here talks to the
network directly: metadata arrives as a :class:`Game` and artwork through the
shared :class:`ImageCache`.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from ..config import (COVER_TILE_HEIGHT, COVER_TILE_WIDTH,
                      HEADER_BANNER_HEIGHT, SCREENSHOT_THUMB_HEIGHT)
from ..services.gameyfin_api import DownloadProvider, Game, GameImage
from ..services.image_cache import ImageCache
from ..settings import SettingsManager
from ..utils import format_size

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Clickable label
# ------------------------------------------------------------------

class ClickableLabel(QLabel):
    """A QLabel that emits *clicked* on mouse press or space/return."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.clicked.emit()
        super().keyPressEvent(event)


# ------------------------------------------------------------------
# Screenshot viewer dialog
# ------------------------------------------------------------------

class ScreenshotViewerDialog(QDialog):
    """Show a screenshot at full size with close button."""

    def __init__(self, image_data: bytes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("Screenshot")
        self.setMinimumSize(600, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setWordWrap(False)
        layout.addWidget(self.image_label)

        close_button = QPushButton("Close")
        close_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignCenter)

        # Display the image
        pixmap = QPixmap()
        if pixmap.loadFromData(image_data):
            self.image_label.setPixmap(pixmap.scaled(
                self.size() * 0.9,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        pixmap = self.image_label.pixmap()
        if pixmap is not None:
            self.image_label.setPixmap(pixmap.scaled(
                self.size() * 0.9,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class GameDetailWidget(QWidget):
    """Detail page for one game, with a download button."""

    back_requested = pyqtSignal()
    download_requested = pyqtSignal(object, str)  # (Game, provider key)

    def __init__(self, image_cache: ImageCache, settings: SettingsManager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image_cache = image_cache
        self.settings = settings
        self.game: Game | None = None
        self.providers: list[DownloadProvider] = []
        # image id -> (label, width, height) for artwork still being fetched
        self._pending_images: dict[int, tuple[QLabel, int, int]] = {}
        # Kept so the header banner can be rescaled when the view is resized
        self._header_data: bytes | None = None
        # Screenshot image data keyed by image id for the viewer dialog
        self._screenshot_images: dict[int, bytes] = {}
        # Reusable viewer dialog
        self._viewer_dialog: ScreenshotViewerDialog | None = None

        self.image_cache.ready.connect(self._on_image_ready)

        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the scrollable detail layout."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 8, 8, 8)
        self.back_button = QPushButton("← Back to library")
        self.back_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.back_button.clicked.connect(self.back_requested.emit)
        top_bar.addWidget(self.back_button)
        top_bar.addStretch()
        outer.addLayout(top_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll)

        content = QWidget()
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(12)

        self.header_label = QLabel()
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.header_label.setMinimumHeight(HEADER_BANNER_HEIGHT)
        self.header_label.setMaximumHeight(HEADER_BANNER_HEIGHT)
        self.header_label.hide()
        layout.addWidget(self.header_label)

        # Cover on the left, textual metadata on the right
        upper = QHBoxLayout()
        upper.setSpacing(16)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(COVER_TILE_WIDTH, COVER_TILE_HEIGHT)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("border-radius: 6px;")
        upper.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignTop)

        info_column = QVBoxLayout()
        info_column.setSpacing(6)

        self.title_label = QLabel()
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        info_column.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        info_column.addWidget(self.subtitle_label)

        self.rating_label = QLabel()
        self.rating_label.setStyleSheet("font-size: 12px;")
        info_column.addWidget(self.rating_label)

        self.detail_labels: dict[str, QLabel] = {}
        for key in ("Platforms", "Genres", "Themes", "Features", "Developers",
                    "Publishers", "Size"):
            row = QHBoxLayout()
            name = QLabel(f"{key}:")
            name.setMinimumWidth(90)
            name.setStyleSheet("font-size: 12px; color: palette(mid);")
            value = QLabel()
            value.setWordWrap(True)
            value.setStyleSheet("font-size: 12px;")
            value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(value, 1)
            info_column.addLayout(row)
            self.detail_labels[key] = value

        download_row = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.download_button.clicked.connect(self._emit_download)
        download_row.addWidget(self.download_button)

        self.provider_combo = QComboBox()
        self.provider_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.provider_combo.hide()
        download_row.addWidget(self.provider_combo)
        download_row.addStretch()
        info_column.addLayout(download_row)

        info_column.addStretch()
        upper.addLayout(info_column, 1)
        layout.addLayout(upper)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_label)

        self.screenshot_heading = QLabel("Screenshots")
        self.screenshot_heading.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.screenshot_heading.hide()
        layout.addWidget(self.screenshot_heading)

        self.screenshot_scroll = QScrollArea()
        self.screenshot_scroll.setWidgetResizable(True)
        self.screenshot_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.screenshot_scroll.setFixedHeight(SCREENSHOT_THUMB_HEIGHT + 24)
        self.screenshot_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.screenshot_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.screenshot_scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.screenshot_scroll.hide()
        screenshot_holder = QWidget()
        self.screenshot_layout = QHBoxLayout(screenshot_holder)
        self.screenshot_layout.setContentsMargins(0, 0, 0, 0)
        self.screenshot_layout.setSpacing(8)
        self.screenshot_layout.addStretch()
        self.screenshot_scroll.setWidget(screenshot_holder)
        layout.addWidget(self.screenshot_scroll)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def set_providers(self, providers: list[DownloadProvider]) -> None:
        """Set the available download providers, showing the picker when >1."""
        self.providers = providers
        self.provider_combo.clear()
        for provider in providers:
            self.provider_combo.addItem(provider.name, provider.key)
        self.provider_combo.setVisible(len(providers) > 1)
        self.download_button.setEnabled(bool(providers))
        if not providers:
            self.download_button.setToolTip("The server reported no download providers")
        else:
            self.download_button.setToolTip("")

    def show_game(self, game: Game) -> None:
        """Populate the view with *game* and start loading its artwork."""
        self.game = game
        self._pending_images.clear()

        self.title_label.setText(game.title)
        self.subtitle_label.setText(" · ".join(self._subtitle_parts(game)))
        self.rating_label.setText(self._rating_text(game))
        self.summary_label.setText(game.summary or "No description available.")

        self.detail_labels["Platforms"].setText(", ".join(game.platforms) or "—")
        self.detail_labels["Genres"].setText(", ".join(game.genres) or "—")
        self.detail_labels["Themes"].setText(", ".join(game.themes) or "—")
        self.detail_labels["Features"].setText(", ".join(game.features) or "—")
        self.detail_labels["Developers"].setText(", ".join(game.developers) or "—")
        self.detail_labels["Publishers"].setText(", ".join(game.publishers) or "—")
        self.detail_labels["Size"].setText(format_size(game.file_size) if game.file_size else "—")

        self.cover_label.clear()
        self.cover_label.setText("No cover")
        if game.cover:
            self._load_image(game.cover, self.cover_label, COVER_TILE_WIDTH, COVER_TILE_HEIGHT)

        self.header_label.clear()
        self._header_data = None
        self.header_label.setVisible(game.header is not None)
        if game.header:
            self._load_image(game.header, self.header_label, 0, HEADER_BANNER_HEIGHT)

        self._rebuild_screenshots(game)

    def _subtitle_parts(self, game: Game) -> list[str]:
        """Return the small grey line under the title (release year, library)."""
        parts = []
        if game.release:
            parts.append(str(game.release))
        if game.platforms:
            parts.append(game.platforms[0])
        return parts

    def _rating_text(self, game: Game) -> str:
        """Return a compact rating line, empty when the server has no ratings."""
        parts = []
        if game.user_rating is not None:
            parts.append(f"Users {game.user_rating}%")
        if game.critic_rating is not None:
            parts.append(f"Critics {game.critic_rating}%")
        return "   ".join(parts)

    def _rebuild_screenshots(self, game: Game) -> None:
        """Replace the screenshot strip with thumbnails for *game*."""
        while self.screenshot_layout.count():
            item = self.screenshot_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        screenshots = [img for img in game.images if img.type.upper() == "SCREENSHOT"]
        if not screenshots:
            screenshots = list(game.images)

        self.screenshot_heading.setVisible(bool(screenshots))
        self.screenshot_scroll.setVisible(bool(screenshots))

        for image in screenshots:
            label = ClickableLabel()
            label.setFixedHeight(SCREENSHOT_THUMB_HEIGHT)
            label.setMinimumWidth(int(SCREENSHOT_THUMB_HEIGHT * 16 / 9))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            label.clicked.connect(lambda img=image: self._open_screenshot(img))
            self.screenshot_layout.addWidget(label)
            self._load_image(image, label, 0, SCREENSHOT_THUMB_HEIGHT)

        self.screenshot_layout.addStretch()

    # ------------------------------------------------------------------
    # Artwork
    # ------------------------------------------------------------------

    def _load_image(self, image: GameImage, label: QLabel, width: int, height: int) -> None:
        """Show *image* in *label*, fetching it in the background when uncached."""
        data = self.image_cache.request(image)
        if data is not None:
            if label is self.header_label:
                self._apply_header(data)
            elif isinstance(label, ClickableLabel):
                # Store raw bytes for the screenshot viewer
                self._screenshot_images[image.id] = data
                self._apply_pixmap(label, data, width, height)
            else:
                self._apply_pixmap(label, data, width, height)
            return
        self._pending_images[image.id] = (label, width, height)

    def _on_image_ready(self, image_id: int, data: bytes) -> None:
        """Apply a background-fetched image to the label that asked for it."""
        target = self._pending_images.pop(image_id, None)
        if target is None:
            return
        label, width, height = target
        if label is self.header_label:
            self._apply_header(data)
            return
        self._apply_pixmap(label, data, width, height)

    def _apply_header(self, data: bytes) -> None:
        """Fill the banner across the full view width, cropping the overflow."""
        self._header_data = data
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        width = max(self.width(), self.header_label.width(), HEADER_BANNER_HEIGHT)
        self.header_label.setPixmap(pixmap.scaled(
            width, HEADER_BANNER_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Rescale the banner so it keeps spanning the view."""
        super().resizeEvent(event)
        if self._header_data is not None:
            self._apply_header(self._header_data)

    @staticmethod
    def _apply_pixmap(label: QLabel, data: bytes, width: int, height: int) -> None:
        """Scale *data* into *label*, keeping the aspect ratio."""
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        if width and height:
            pixmap = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        elif height:
            pixmap = pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
        label.setText("")
        label.setPixmap(pixmap)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def selected_provider_key(self) -> str:
        """Return the chosen download provider key, or an empty string."""
        if not self.providers:
            return ""
        data = self.provider_combo.currentData()
        return str(data) if data else self.providers[0].key

    def _emit_download(self) -> None:
        """Ask the parent to start a download for the shown game."""
        if self.game is None:
            return
        provider_key = self.selected_provider_key()
        if not provider_key:
            logger.warning("Download requested but no provider is available")
            return
        self.download_requested.emit(self.game, provider_key)

    def _open_screenshot(self, image: GameImage) -> None:
        """Show *image* in a full-size viewer dialog."""
        data = self._screenshot_images.get(image.id)
        if data is None:
            # Try to fetch it now; the viewer will open when the image arrives
            self.image_cache.request(image)
            self.image_cache.ready.connect(
                lambda img_id, img_data: self._show_screenshot(img_id, img_data),
            )
            return
        self._show_screenshot(image.id, data)

    def _show_screenshot(self, image_id: int, data: bytes) -> None:
        """Display the screenshot in the reusable dialog."""
        if self._viewer_dialog is None:
            self._viewer_dialog = ScreenshotViewerDialog(data, self)
            self._viewer_dialog.finished.connect(self._on_viewer_closed)
        else:
            self._viewer_dialog.image_label.clear()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._viewer_dialog.image_label.setPixmap(pixmap.scaled(
                    self._viewer_dialog.size() * 0.9,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        self._viewer_dialog.show()
        self._viewer_dialog.raise_()
        self._viewer_dialog.activateWindow()

    def _on_viewer_closed(self) -> None:
        """Recycle the dialog for the next screenshot."""
        self._viewer_dialog = None
