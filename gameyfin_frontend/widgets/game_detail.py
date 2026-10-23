"""Native detail view for a single game.

Shows everything the server reports for a game — header art, cover, summary,
release date, ratings, genres/themes, platforms, developers/publishers, file
size and screenshots — plus the download button and, for installed games,
a Play button. Two presentations share those controls: the game as a 3D box
you can turn over (:mod:`.game_box`, the default) and the classic metadata
page; the ``GF_DETAIL_BOX_VIEW`` setting picks which one. Nothing here talks to the
network directly: metadata arrives as a :class:`Game` and artwork through the
shared :class:`ImageCache`.
"""

import logging
import os

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from ..config import (BOX_DISC_TEXTURE_SIZE, BOX_SIDE_PANEL_WIDTH, COVER_TILE_HEIGHT, COVER_TILE_WIDTH,
                      HEADER_BANNER_HEIGHT, SCREENSHOT_THUMB_HEIGHT)
from ..services.gameyfin_api import DownloadProvider, Game, GameImage
from ..services.image_cache import ImageCache
from ..services.installed_games import InstalledGame
from ..settings import SettingsManager
from ..utils import accent_color, contrasting_text_color, format_size, muted_text_color
from .game_box import (DescriptionPage, GameBoxView, _format_release, edge_color, render_back_cover,
                       render_front_placeholder, render_inner_flap, render_spine)
from .game_disc import render_disc

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
    play_requested = pyqtSignal(str)  # launch script path
    stop_requested = pyqtSignal()  # stop the running installed game
    # The user picked another launch script (path), so it can be remembered
    script_selected = pyqtSignal(str)
    # The box view's download animation ended (played out or skipped)
    download_animation_finished = pyqtSignal()

    def __init__(self, image_cache: ImageCache, settings: SettingsManager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image_cache = image_cache
        self.settings = settings
        self.game: Game | None = None
        self.installed: InstalledGame | None = None
        self.running = False
        # Set while the server is unreachable: nothing can be downloaded
        self.offline = False
        self.providers: list[DownloadProvider] = []
        # image id -> (label, width, height) for artwork still being fetched
        self._pending_images: dict[int, tuple[QLabel, int, int]] = {}
        # Kept so the header banner can be rescaled when the view is resized
        self._header_data: bytes | None = None
        # Screenshot image data keyed by image id for the viewer dialog
        self._screenshot_images: dict[int, bytes] = {}
        # Reusable viewer dialog
        self._viewer_dialog: ScreenshotViewerDialog | None = None
        # Image id the user clicked while its bytes were still being fetched
        self._awaited_screenshot: int | None = None
        # Screenshots of the shown game, in strip / box-back order
        self._screenshots: list[GameImage] = []
        # The screenshots actually laid out on the box back, in slot order
        self._box_shot_order: list[GameImage] = []
        # Bytes of every artwork the 3D box is painted from, by image id
        self._box_images: dict[int, bytes] = {}
        # Artwork arrives one image at a time: repaint the box once per burst
        self._box_refresh = QTimer(self)
        self._box_refresh.setSingleShot(True)
        self._box_refresh.setInterval(80)
        self._box_refresh.timeout.connect(self._refresh_box)

        self.image_cache.ready.connect(self._on_image_ready)
        self.image_cache.failed.connect(self._on_image_failed)

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

        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pages.addWidget(self.scroll)

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
        self.subtitle_label.setStyleSheet(f"font-size: 12px; color: {muted_text_color(self)};")
        info_column.addWidget(self.subtitle_label)

        self.rating_label = QLabel()
        self.rating_label.setStyleSheet("font-size: 12px;")
        info_column.addWidget(self.rating_label)

        self.detail_labels: dict[str, QLabel] = {}
        self._muted_labels: list[QLabel] = [self.subtitle_label]
        for key in ("Platforms", "Genres", "Themes", "Features", "Developers",
                    "Publishers", "Size"):
            row = QHBoxLayout()
            name = QLabel(f"{key}:")
            name.setMinimumWidth(90)
            name.setStyleSheet(f"font-size: 12px; color: {muted_text_color(self)};")
            value = QLabel()
            value.setWordWrap(True)
            value.setStyleSheet("font-size: 12px;")
            value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(value, 1)
            info_column.addLayout(row)
            self.detail_labels[key] = value
            self._muted_labels.append(name)

        # Play/Download controls; moved between the two pages by set_box_mode
        self.actions_widget = QWidget()
        download_row = QHBoxLayout(self.actions_widget)
        self._actions_row = download_row
        download_row.setContentsMargins(0, 0, 0, 0)
        self.play_button = QPushButton("Play")
        self.play_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.play_button.clicked.connect(self._emit_play)
        self.play_button.hide()
        download_row.addWidget(self.play_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.stop_button.setToolTip("Close the game and everything running in its prefix")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.hide()
        download_row.addWidget(self.stop_button)

        self.script_combo = QComboBox()
        self.script_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.script_combo.activated.connect(self._on_script_activated)
        self.script_combo.hide()
        download_row.addWidget(self.script_combo)

        self.download_button = QPushButton("Download")
        self.download_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.download_button.clicked.connect(self._emit_download)
        download_row.addWidget(self.download_button)

        self.provider_combo = QComboBox()
        self.provider_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.provider_combo.hide()
        download_row.addWidget(self.provider_combo)
        download_row.addStretch()
        self._classic_actions_slot = info_column
        info_column.addWidget(self.actions_widget)

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

        self._build_box_page()
        self.set_box_mode(bool(int(self.settings.get("GF_DETAIL_BOX_VIEW", 1) or 0)))

    def _build_box_page(self) -> None:
        """The 3D box with a slim column for the title and the actions."""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(16, 0, 16, 16)
        row.setSpacing(16)

        self.box_view = GameBoxView()
        self.box_view.side_changed.connect(self._on_box_side_changed)
        self.box_view.screenshot_clicked.connect(self._on_box_screenshot_clicked)
        self.box_view.cover_changed.connect(self._on_box_cover_changed)
        self.box_view.disc_finished.connect(self.download_animation_finished.emit)
        row.addWidget(self.box_view, 1)

        side = QWidget()
        side.setFixedWidth(BOX_SIDE_PANEL_WIDTH)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addStretch()

        self.box_title_label = QLabel()
        self.box_title_label.setWordWrap(True)
        self.box_title_label.setStyleSheet("font-size: 26px; font-weight: bold;")
        column.addWidget(self.box_title_label)

        self.box_subtitle_label = QLabel()
        self.box_subtitle_label.setWordWrap(True)
        column.addWidget(self.box_subtitle_label)
        self._muted_labels.append(self.box_subtitle_label)

        self.box_rating_label = QLabel()
        self.box_rating_label.setStyleSheet("font-size: 12px;")
        column.addWidget(self.box_rating_label)

        # Title block, then the actions as one aligned block of full-width rows
        self._box_actions_slot = column
        column.addSpacing(12)

        self.flip_button = QPushButton("Turn box over")
        self.flip_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.flip_button.clicked.connect(lambda: self.box_view.flip())
        # Inserted below the actions; kept as the last row before the hint
        self._flip_row = QHBoxLayout()
        self._flip_row.addWidget(self.flip_button, 1)

        # Opens the front cover like a book to read the full description
        self.cover_button = QPushButton("Open the box")
        self.cover_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cover_button.clicked.connect(lambda: self.box_view.toggle_cover())
        self._flip_row.addWidget(self.cover_button, 1)

        # One short line per gesture rather than a sentence that wraps mid-phrase
        self.box_hint_label = QLabel("Drag to turn the box<br>"
                                     "Click the cover to open it<br>"
                                     "Click the back to turn it over")
        self.box_hint_label.setTextFormat(Qt.TextFormat.RichText)
        self._muted_labels.append(self.box_hint_label)

        column.addLayout(self._flip_row)
        column.addSpacing(8)
        column.addWidget(self.box_hint_label)
        column.addStretch()
        row.addWidget(side)

        self.box_page = page
        self.pages.addWidget(page)

    def set_box_mode(self, enabled: bool) -> None:
        """Switch between the 3D box and the classic metadata page."""
        self.box_mode = enabled
        self._box_actions_slot.removeWidget(self.actions_widget)
        self._classic_actions_slot.removeWidget(self.actions_widget)
        if enabled:
            # Right under the rating line, above the flip button
            index = self._box_actions_slot.indexOf(self.box_rating_label) + 1
            self._box_actions_slot.insertWidget(index, self.actions_widget)
        else:
            index = self._classic_actions_slot.count() - 1  # before the stretch
            self._classic_actions_slot.insertWidget(index, self.actions_widget)
        # In the narrow side column the buttons fill its width so their edges line
        # up; on the classic page they keep their natural size
        for button in (self.play_button, self.download_button):
            self._actions_row.setStretchFactor(button, 1 if enabled else 0)
        self._actions_row.setStretch(self._actions_row.count() - 1, 0 if enabled else 1)
        self.pages.setCurrentWidget(self.box_page if enabled else self.scroll)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Pick up the theme's resolved colours once the widget is polished."""
        super().showEvent(event)
        self.refresh_theme_colors()

    def refresh_theme_colors(self) -> None:
        """Re-apply palette-derived colours after the theme changed."""
        for label in self._muted_labels:
            label.setStyleSheet(f"font-size: 12px; color: {muted_text_color(self)};")
        self._style_primary_action()
        self.box_view.update()

    def _style_primary_action(self) -> None:
        """Fill the main action with the theme's accent so it stands out.

        That is Play once the game is installed, Download otherwise; every other
        button keeps the theme's own (secondary) look.
        """
        primary = self.play_button if self.installed is not None else self.download_button
        accent = accent_color(self)
        text = contrasting_text_color(accent)

        def rgba(colour, alpha):
            return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {alpha})"

        style = (
            f"QPushButton {{ background-color: {accent.name()}; color: {text.name()};"
            f" border: 1px solid {accent.name()}; border-radius: 4px;"
            # Only the sides: the height stays the theme's, so a row lines up
            f" padding-left: 20px; padding-right: 20px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {accent.lighter(112).name()};"
            f" border-color: {accent.lighter(112).name()}; }}"
            f"QPushButton:pressed {{ background-color: {accent.darker(115).name()}; }}"
            f"QPushButton:focus {{ border: 2px solid {rgba(text, 160)}; }}"
            f"QPushButton:disabled {{ background-color: {rgba(accent, 70)};"
            f" color: {rgba(text, 130)}; border-color: transparent; }}"
        )
        for button in (self.play_button, self.download_button):
            button.setStyleSheet(style if button is primary else "")
        # Themes draw outlined buttons with different border widths; borrow the
        # height of a plain one so the filled button lines up beside it
        self.download_button.setMinimumHeight(0)
        self.play_button.setMinimumHeight(0)
        primary.setMinimumHeight(self.cover_button.sizeHint().height())

    def set_providers(self, providers: list[DownloadProvider]) -> None:
        """Set the available download providers, showing the picker when >1."""
        self.providers = providers
        self.provider_combo.clear()
        for provider in providers:
            self.provider_combo.addItem(provider.name, provider.key)
        self._update_download_state()

    def set_offline(self, offline: bool) -> None:
        """Disable downloading while the server cannot be reached."""
        self.offline = offline
        self._update_download_state()

    def _update_download_state(self) -> None:
        providers = self.providers
        self.provider_combo.setVisible(len(providers) > 1 and not self.offline)
        self.download_button.setEnabled(bool(providers) and not self.offline)
        if self.offline:
            self.download_button.setToolTip("Offline: the server can't be reached")
        elif not providers:
            self.download_button.setToolTip("The server reported no download providers")
        else:
            self.download_button.setToolTip("")

    def show_game(self, game: Game) -> None:
        """Populate the view with *game* and start loading its artwork."""
        self.game = game
        self.set_installed(None)
        self._pending_images.clear()

        self.title_label.setText(game.title)
        self.subtitle_label.setText(" · ".join(self._subtitle_parts(game)))
        self.rating_label.setText(self._rating_text(game))
        self.box_title_label.setText(game.title)
        # The box prints the date in the locale's format; match it beside the box
        box_parts = self._subtitle_parts(game)
        if game.release and box_parts:
            box_parts[0] = _format_release(game.release)
        self.box_subtitle_label.setText(" · ".join(box_parts))
        self.box_rating_label.setText(self.rating_label.text())
        self.box_rating_label.setVisible(bool(self.rating_label.text()))
        self._box_images.clear()
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

        self._box_refresh.stop()
        self._refresh_box()
        self.box_view.reset_pose(intro=self.isVisible() and self.box_mode)

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
        # Drop the previous game's bytes and any pending click on them
        self._screenshot_images.clear()
        self._awaited_screenshot = None
        while self.screenshot_layout.count():
            item = self.screenshot_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        screenshots = [img for img in game.images if img.type.upper() == "SCREENSHOT"]
        if not screenshots:
            screenshots = list(game.images)
        self._screenshots = screenshots

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
            self._box_images[image.id] = data
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
        if target is not None:
            self._box_images[image_id] = data
            self._box_refresh.start()
            label, width, height = target
            if label is self.header_label:
                self._apply_header(data)
                return
            if isinstance(label, ClickableLabel):
                # Store raw bytes for the screenshot viewer
                self._screenshot_images[image_id] = data
            self._apply_pixmap(label, data, width, height)
        if image_id == self._awaited_screenshot:
            self._awaited_screenshot = None
            self._show_screenshot(image_id, data)

    def _on_image_failed(self, image_id: int, _message: str) -> None:
        """Drop a failed fetch from the pending set so it can be retried later."""
        self._pending_images.pop(image_id, None)
        if image_id == self._awaited_screenshot:
            self._awaited_screenshot = None

    # ------------------------------------------------------------------
    # 3D box
    # ------------------------------------------------------------------

    def _box_image(self, image: GameImage | None) -> QImage | None:
        if image is None or image.id not in self._box_images:
            return None
        decoded = QImage()
        return decoded if decoded.loadFromData(self._box_images[image.id]) else None

    def _refresh_box(self) -> None:
        """Repaint the box's faces from whatever artwork has arrived so far."""
        game = self.game
        if game is None:
            return
        cover = self._box_image(game.cover)
        side_color = edge_color(cover)
        view = self.box_view
        if cover is not None:
            view.set_front(cover, side_color)
        else:
            width, height = view.back_texture_size()
            view.set_front(render_front_placeholder(game, width, height, side_color), side_color)

        width, height = view.back_texture_size()
        shots = [self._box_image(image) for image in self._screenshots[:4]]
        loaded = [shot for shot in shots if shot is not None]
        hero = self._box_image(game.header) or (loaded[0] if loaded else cover)
        # Only screenshots that already arrived are laid out, so keep a map
        # from the back cover's slots back to the game's screenshots
        self._box_shot_order = [image for image, shot in zip(self._screenshots[:4], shots)
                                if shot is not None]
        view.set_back(render_back_cover(game, width, height, side_color, hero, loaded))
        view.set_spine(render_spine(game, *view.spine_texture_size(), side_color))
        # Different art inside than on the back, when there is any
        inside_art = (loaded[0] if loaded else None) or self._box_image(game.header) or cover
        view.set_inside(render_inner_flap(game, width, height, side_color, inside_art),
                        DescriptionPage(game.title, game.summary, width, height, side_color))
        view.set_disc(render_disc(game, cover, BOX_DISC_TEXTURE_SIZE, side_color))

    def _on_box_cover_changed(self, opened: bool) -> None:
        self.cover_button.setText("Close the box" if opened else "Open the box")

    def _on_box_side_changed(self, showing_back: bool) -> None:
        self.flip_button.setText("Show the front" if showing_back else "Turn box over")

    def _on_box_screenshot_clicked(self, index: int) -> None:
        if 0 <= index < len(self._box_shot_order):
            self._open_screenshot(self._box_shot_order[index])

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
        if self.box_mode and self.box_view.isVisible():
            # Purely decorative, but started first so whoever handles the
            # request can tell it is playing (and wait with switching tabs)
            self.box_view.insert_disc()
        self.download_requested.emit(self.game, provider_key)

    def is_playing_download_animation(self) -> bool:
        return self.box_view.is_inserting_disc()

    def cancel_download_animation(self) -> None:
        """Stop the animation for a download that didn't start after all."""
        self.box_view.blockSignals(True)
        self.box_view.skip_disc()
        self.box_view.blockSignals(False)

    def set_installed(self, info: InstalledGame | None) -> None:
        """Show the Play button (and script picker) for an installed game."""
        self.installed = info
        self.running = False
        self._style_primary_action()
        self.script_combo.clear()
        if info is None:
            self.play_button.hide()
            self.stop_button.hide()
            self.script_combo.hide()
            return

        for script in info.scripts:
            self.script_combo.addItem(os.path.splitext(os.path.basename(script))[0], script)
        if info.last_script:
            for row, script in enumerate(info.scripts):
                if os.path.basename(script) == info.last_script:
                    self.script_combo.setCurrentIndex(row)
                    break
        self.script_combo.setVisible(len(info.scripts) > 1)

        self.play_button.show()
        self._update_play_state()

    def set_running(self, running: bool) -> None:
        """Switch Play to its "Running…" state and show Stop while the game runs."""
        if self.installed is None:
            return
        # The focused button is about to be disabled or hidden: hand the focus
        # (and with it the gamepad) to its counterpart
        play_focused = self.play_button.hasFocus()
        stop_focused = self.stop_button.hasFocus()
        self.running = running
        self._update_play_state()
        if running and play_focused:
            self.stop_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif not running and stop_focused:
            self.play_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_play_state(self) -> None:
        scripts = bool(self.installed and self.installed.scripts)
        self.play_button.setText("Running…" if self.running else "Play")
        self.play_button.setEnabled(scripts and not self.running)
        self.script_combo.setEnabled(not self.running)
        self.stop_button.setVisible(self.running)
        self.play_button.setToolTip(
            "" if scripts
            else "No launch scripts — create shortcuts in the Prefixes tab"
        )

    def selected_script(self) -> str:
        """Return the launch script Play would run, or an empty string."""
        data = self.script_combo.currentData()
        return str(data) if data else ""

    def _on_script_activated(self, _index: int) -> None:
        script = self.selected_script()
        if script:
            self.script_selected.emit(script)

    def _emit_play(self) -> None:
        """Ask the parent to launch the selected script of the installed game."""
        script = self.selected_script()
        if script:
            self.play_requested.emit(script)

    def _open_screenshot(self, image: GameImage) -> None:
        """Show *image* in a full-size viewer dialog."""
        data = self._screenshot_images.get(image.id)
        if data is None:
            # Not fetched yet: take it from the cache, or wait for this one
            # image only -- ``_on_image_ready`` opens the viewer when it lands.
            data = self.image_cache.request(image)
            if data is None:
                self._awaited_screenshot = image.id
                return
            self._screenshot_images[image.id] = data
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
