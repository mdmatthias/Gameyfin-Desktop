import json
import logging
import os
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import (QApplication, QGridLayout, QWidget, QScrollArea, QVBoxLayout, QPushButton, QHBoxLayout, QSpacerItem, QSizePolicy, QLabel, QProgressBar)

from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.widgets.download_item import DownloadItemWidget
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.services import DownloadHistoryService

logger = logging.getLogger(__name__)


class DownloadManagerWidget(QWidget):

    def __init__(self, umu_database: UmuDatabase, parent: QWidget | None = None, settings: SettingsManager | None = None):
        """Create the download manager widget with a scrollable grid of download items.

        Loads persisted download history from JSON on startup.

        Args:
            umu_database: UmuDatabase instance for UMU lookups.
            parent: Parent widget.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__(parent)
        self.umu_database = umu_database
        self.prefix_manager = None
        self.tray = None  # Set by GameyfinTray after init
        self.settings = settings
        self.download_history = DownloadHistoryService(
            settings.get_downloads_json_path()
        ) if settings else None
        self.download_records: list[dict[str, Any]] = []
        self.widget_map: dict[DownloadItemWidget, list[QWidget]] = {}

        self.main_layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()

        self.downloads_layout = QGridLayout(self.scroll_content)

        self.downloads_layout.setColumnStretch(1, 4)
        self.downloads_layout.setColumnStretch(2, 2)
        self.downloads_layout.setColumnStretch(3, 2)
        self.downloads_layout.setColumnStretch(4, 1)

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        self.load_history()

    @staticmethod
    def _is_button(widget: QWidget) -> bool:
        """Return True if the widget is a QPushButton."""
        return isinstance(widget, QPushButton)

    def _all_visible_buttons(self) -> list[QPushButton]:
        """Collect every visible QPushButton across all download rows."""
        buttons: list[QPushButton] = []
        for row in range(self.downloads_layout.rowCount() - 1):  # skip stretch
            for col in range(self.downloads_layout.columnCount()):
                item = self.downloads_layout.itemAtPosition(row, col)
                if item and item.widget():
                    w = item.widget()
                    if self._is_button(w) and w.isVisible():
                        buttons.append(w)
                    else:
                        # Grab child buttons from containers like button_container
                        for child in w.children():
                            if isinstance(child, QPushButton) and child.isVisible():
                                buttons.append(child)
        return buttons

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle Tab / Shift+Tab by cycling focus through visible download buttons only."""
        if event.key() == Qt.Key.Key_Tab or event.key() == Qt.Key.Key_Backtab:
            buttons = self._all_visible_buttons()
            if not buttons:
                super().keyPressEvent(event)
                return

            current = QApplication.focusWidget()
            try:
                idx = buttons.index(current)
            except ValueError:
                idx = -1

            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Tab → previous button (wrap around)
                new_idx = (idx - 1) % len(buttons)
            else:
                # Tab → next button (wrap around)
                new_idx = (idx + 1) % len(buttons)

            buttons[new_idx].setFocus(Qt.FocusReason.TabFocusReason)
            return

        super().keyPressEvent(event)

    def add_download_to_grid(self, controller: DownloadItemWidget) -> None:
        """Adds a download item widget to the grid layout at the next available row."""
        row = self.downloads_layout.rowCount()
        widgets = controller.get_widgets_for_grid()
        self.downloads_layout.addWidget(widgets[0], row, 0)
        self.downloads_layout.addWidget(widgets[1], row, 1)
        self.downloads_layout.addWidget(widgets[2], row, 2)
        self.downloads_layout.addWidget(widgets[3], row, 3)
        self.downloads_layout.addWidget(widgets[4], row, 4)
        self.widget_map[controller] = widgets

    def add_download(self, worker: StreamDownloadWorker, record: dict[str, Any]) -> None:
        """Add a new download to the grid and persist it to history."""
        controller = DownloadItemWidget(
            self.umu_database, worker=worker, record=record,
            settings=self.settings, tray=self.tray,
        )

        controller.finished.connect(self.on_download_finished)
        controller.installation_finished.connect(self.on_installation_finished)
        controller.remove_requested.connect(self.remove_download_item)

        existing_record = self.download_history.find_by_url(self.download_records, record["url"]) if self.download_history else None
        if existing_record:
            existing_controller = self.find_controller_by_record(existing_record)
            if existing_controller:
                self.remove_download_item(existing_controller)

        self.insert_row_at(0, controller)
        if controller.record not in self.download_records:
            self.download_records.insert(0, controller.record)

        self.save_history()

    def on_download_finished(self, record: dict[str, Any]) -> None:
        """Save download history, refresh prefix list, and notify on completion."""
        self.save_history()
        if self.prefix_manager:
            self.prefix_manager.refresh_prefixes()

        filename = record.get("filename", "Unknown")
        error_msg = record.pop("_error_message", None)

        if error_msg:
            self._notify(f"Download failed: {filename}", str(error_msg))
        elif record.get("status") == "Completed":
            self._notify(f"Download complete: {filename}", f"{filename} has been downloaded.")

    def _notify(self, title: str, message: str) -> None:
        """Show a desktop notification via the tray, respecting user preferences."""
        if self.tray is not None:
            self.tray.show_notification(title, message, enabled_key="GF_DOWNLOAD_NOTIFICATIONS")

    def on_installation_finished(self, game_name: str) -> None:
        """Refresh the prefix list after a game installation completes.

        Args:
            game_name: The name of the game that was just installed (carried via signal).
        """
        if self.prefix_manager:
            self.prefix_manager.refresh_prefixes()

    def load_history(self) -> None:
        """Load persisted download history from JSON and recreate widgets for each record."""
        try:
            if self.download_history:
                self.download_records = self.download_history.load()

                for record in reversed(self.download_records):
                    controller = DownloadItemWidget(self.umu_database, record=record, settings=self.settings)
                    controller.remove_requested.connect(self.remove_download_item)
                    self.add_download_to_grid(controller)

            # Add a stretch row at the bottom to push all items to the top
            self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)

        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error loading download history: %s", e)
            self.download_records = []

    def save_history(self) -> None:
        """Persist the current download list to JSON, preserving grid order."""
        try:
            # Rebuild records from widget_map to ensure correct order
            label_to_controller = {
                widgets[1]: controller
                for controller, widgets in self.widget_map.items()
            }

            records = []
            for row in range(self.downloads_layout.rowCount() - 1):  # -1 to skip stretch
                item = self.downloads_layout.itemAtPosition(row, 1)
                if item:
                    widget = item.widget()
                    if not widget:
                        continue
                    controller = label_to_controller.get(widget)
                    if controller:
                        records.append(controller.record)

            self.download_records = records

            if self.download_history:
                self.download_history.save(records)
        except OSError as e:
            logger.error("Error saving download history: %s", e)

    def closeEvent(self, event: QCloseEvent):
        """Persist download history before the widget is closed."""
        self.save_history()
        event.accept()

    def find_controller_by_url(self, url: str) -> DownloadItemWidget | None:
        """Find a download controller by its URL for duplicate detection."""
        for controller in self.widget_map.keys():
            if controller.record.get("url") == url:
                return controller
        return None

    def find_controller_by_record(self, record: dict[str, Any]) -> DownloadItemWidget | None:
        """Find a download controller by its record dict."""
        for controller in self.widget_map.keys():
            if controller.record is record:
                return controller
        return None

    def remove_download_item(self, controller: DownloadItemWidget) -> None:
        """Remove a download widget from the grid and clean up its resources."""
        if controller not in self.widget_map:
            return

        # Remove the old stretch row (it's at the last index)
        current_row_count = self.downloads_layout.rowCount()
        self.downloads_layout.setRowStretch(current_row_count - 1, 0)

        widgets_to_remove = self.widget_map.pop(controller)

        row_to_remove = -1
        for i in range(current_row_count):
            item = self.downloads_layout.itemAtPosition(i, 0)
            if item and item.widget() == widgets_to_remove[0]:
                row_to_remove = i
                break

        if row_to_remove == -1:
            # Item not found, re-add stretch and exit
            self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)
            controller.deleteLater()  # Still delete the controller
            self.save_history()  # Re-save
            return

        # Remove all widgets from the specified row
        for col in range(self.downloads_layout.columnCount()):
            item = self.downloads_layout.itemAtPosition(row_to_remove, col)
            if item:
                widget = item.widget()
                if widget:
                    self.downloads_layout.removeWidget(widget)
                    widget.deleteLater()

        # Shift all subsequent rows up
        for row in range(row_to_remove + 1, self.downloads_layout.rowCount()):
            for col in range(self.downloads_layout.columnCount()):
                item = self.downloads_layout.itemAtPosition(row, col)
                if item:
                    # Take the item from its current position
                    taken_item = self.downloads_layout.takeAt(self.downloads_layout.indexOf(item))
                    if taken_item:
                        # Add it to the row above
                        self.downloads_layout.addItem(taken_item, row - 1, col)

        self.downloads_layout.setRowStretch(self.downloads_layout.rowCount() - 1, 1)

        controller.deleteLater()
        self.save_history()

    def insert_row_at(self, row_index: int, controller: DownloadItemWidget) -> None:
        """Insert a download widget at a specific grid row, shifting existing rows down.

        Args:
            row_index: The row index to insert at.
            controller: The DownloadItemWidget to insert.
        """
        # Remove the old stretch row
        current_row_count = self.downloads_layout.rowCount()
        if current_row_count > 0:
            self.downloads_layout.setRowStretch(current_row_count - 1, 0)

        # Shift all existing rows down by one
        for row in range(current_row_count - 2, row_index - 1, -1):
            for col in range(self.downloads_layout.columnCount()):
                item = self.downloads_layout.itemAtPosition(row, col)
                if item:
                    taken_item = self.downloads_layout.takeAt(self.downloads_layout.indexOf(item))
                    if taken_item:
                        self.downloads_layout.addItem(taken_item, row + 1, col)

        widgets = controller.get_widgets_for_grid()
        self.downloads_layout.addWidget(widgets[0], row_index, 0)
        self.downloads_layout.addWidget(widgets[1], row_index, 1)
        self.downloads_layout.addWidget(widgets[2], row_index, 2)
        self.downloads_layout.addWidget(widgets[3], row_index, 3)
        self.downloads_layout.addWidget(widgets[4], row_index, 4)

        # Re-add the stretch row at the new bottom
        self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)

        self.widget_map[controller] = widgets
