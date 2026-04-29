import json
import os

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (QGridLayout, QWidget, QScrollArea, QVBoxLayout, QPushButton, QHBoxLayout, QSpacerItem, QSizePolicy)

from gameyfin_frontend.settings import settings_manager
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.widgets.download_item import DownloadItemWidget


class DownloadManagerWidget(QWidget):

    def __init__(self, umu_database: UmuDatabase, parent=None):
        super().__init__(parent)
        self.umu_database = umu_database
        self.prefix_manager = None
        self.json_path = settings_manager.get_downloads_json_path()
        self.download_records = []
        self.widget_map = {}

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

    def add_download_to_grid(self, controller: DownloadItemWidget):
        row = self.downloads_layout.rowCount()
        widgets = controller.get_widgets_for_grid()
        self.downloads_layout.addWidget(widgets[0], row, 0)
        self.downloads_layout.addWidget(widgets[1], row, 1)
        self.downloads_layout.addWidget(widgets[2], row, 2)
        self.downloads_layout.addWidget(widgets[3], row, 3)
        self.downloads_layout.addWidget(widgets[4], row, 4)
        self.widget_map[controller] = widgets

    def add_download(self, worker, record: dict):
        controller = DownloadItemWidget(self.umu_database, worker=worker, record=record)

        controller.finished.connect(self.on_download_finished)
        controller.installation_finished.connect(self.on_installation_finished)
        controller.remove_requested.connect(self.remove_download_item)

        existing_controller = self.find_controller_by_url(record["url"])
        if existing_controller:
            self.remove_download_item(existing_controller)

        self.insert_row_at(0, controller)
        if controller.record not in self.download_records:
            self.download_records.insert(0, controller.record)

        self.save_history()

    def on_download_finished(self, record: dict):
        self.save_history()
        if self.prefix_manager:
            self.prefix_manager.refresh_prefixes()

    def on_installation_finished(self):
        if self.prefix_manager:
            self.prefix_manager.refresh_prefixes()

    def load_history(self):
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    self.download_records = json.load(f)

                for record in reversed(self.download_records):
                    if record["status"] == "Downloading":
                        record["status"] = "Failed"

                    controller = DownloadItemWidget(self.umu_database, record=record)
                    controller.remove_requested.connect(self.remove_download_item)
                    self.add_download_to_grid(controller)

            # Add a stretch row at the bottom to push all items to the top
            self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)

        except Exception as e:
            print(f"Error loading download history: {e}")
            self.download_records = []

    def save_history(self):
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

            with open(self.json_path, 'w') as f:
                json.dump(self.download_records, f, indent=4)
        except Exception as e:
            print(f"Error saving download history: {e}")

    def closeEvent(self, event: QCloseEvent):
        self.save_history()
        event.accept()

    def find_controller_by_url(self, url: str) -> DownloadItemWidget | None:
        for controller in self.widget_map.keys():
            if controller.record.get("url") == url:
                return controller
        return None

    def remove_download_item(self, controller: DownloadItemWidget):
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

    def insert_row_at(self, row_index: int, controller: DownloadItemWidget):
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
