import json
import logging
import os
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QScrollArea,
                             QListWidget, QListWidgetItem, QPushButton,
                             QAbstractItemView)

from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.widgets.download_item import DownloadItemWidget
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.services import DownloadHistoryService

logger = logging.getLogger(__name__)


class DownloadManagerWidget(QWidget):

    def __init__(self, umu_database: UmuDatabase, parent: QWidget | None = None, settings: SettingsManager | None = None):
        """Create the download manager widget with a scrollable list of download items.

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
        self.widget_map: dict[DownloadItemWidget, QListWidgetItem] = {}

        self.main_layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()

        self.list_widget = QListWidget(self.scroll_content)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        # Layout inside scroll content
        layout = QVBoxLayout(self.scroll_content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list_widget)

        self.load_history()

    @staticmethod
    def _is_button(widget: QWidget) -> bool:
        """Return True if the widget is a QPushButton."""
        return isinstance(widget, QPushButton)

    def _all_visible_buttons(self) -> list[QPushButton]:
        """Collect every visible QPushButton across all list items."""
        buttons: list[QPushButton] = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            if widget:
                for child in widget.findChildren(QPushButton):
                    if child.isVisible():
                        buttons.append(child)
        return buttons

    def add_download_to_list(self, controller: DownloadItemWidget) -> None:
        """Adds a download item widget to the list at the last row."""
        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(controller.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, controller)
        self.widget_map[controller] = item

    def add_download(self, worker: StreamDownloadWorker, record: dict[str, Any]) -> None:
        """Add a new download to the list and persist it to history."""
        controller = DownloadItemWidget(
            self.umu_database, worker=worker, record=record,
            settings=self.settings, tray=self.tray,
        )

        controller.finished.connect(self.on_download_finished)
        controller.installation_finished.connect(self.on_installation_finished)
        controller.remove_requested.connect(self.remove_download_item)

        # When the worker renames the extracted folder, update the record path
        worker._path_updated.connect(lambda new_path: record.__setitem__("path", new_path))

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
                    self.add_download_to_list(controller)

        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error loading download history: %s", e)
            self.download_records = []

    def save_history(self) -> None:
        """Persist the current download list to JSON, preserving list order."""
        try:
            records = []
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                widget = self.list_widget.itemWidget(item)
                if widget and isinstance(widget, DownloadItemWidget):
                    records.append(widget.record)

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
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            if widget and isinstance(widget, DownloadItemWidget):
                if widget.record.get("url") == url:
                    return widget
        return None

    def find_controller_by_record(self, record: dict[str, Any]) -> DownloadItemWidget | None:
        """Find a download controller by its record dict."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            if widget and isinstance(widget, DownloadItemWidget):
                if widget.record is record:
                    return widget
        return None

    def _get_item_for_controller(self, controller: DownloadItemWidget) -> QListWidgetItem | None:
        """Find the QListWidgetItem that hosts the given controller via widget_map."""
        return self.widget_map.get(controller)

    def remove_download_item(self, controller: DownloadItemWidget) -> None:
        """Remove a download widget from the list and clean up its resources."""
        item = self._get_item_for_controller(controller)
        if item is None:
            # Fallback: search the list (e.g. for controllers added before widget_map existed)
            for i in range(self.list_widget.count()):
                it = self.list_widget.item(i)
                widget = self.list_widget.itemWidget(it)
                if widget is controller:
                    item = it
                    break

        if item is None:
            return

        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)
        del self.widget_map[controller]
        controller.deleteLater()
        self.save_history()

    def insert_row_at(self, row_index: int, controller: DownloadItemWidget) -> None:
        """Insert a download widget at a specific list row, shifting existing items down.

        Args:
            row_index: The row index to insert at.
            controller: The DownloadItemWidget to insert.
        """
        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(controller.sizeHint())
        self.list_widget.insertItem(row_index, item)
        self.list_widget.setItemWidget(item, controller)

        self.widget_map = getattr(self, 'widget_map', {})
        self.widget_map[controller] = item
