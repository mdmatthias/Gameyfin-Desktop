import json
import os
import time

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWidgets import QGridLayout, QWidget, QScrollArea, QVBoxLayout, QStyle, QStackedLayout, QHBoxLayout, \
    QPushButton, QLabel, QProgressBar


class DownloadItemWidget(QWidget):
    remove_requested = pyqtSignal(QWidget)
    finished = pyqtSignal(dict)

    def __init__(self, download_item: QWebEngineDownloadRequest = None, record: dict = None, initial_total_size: int = 0, parent=None):
        super().__init__(parent)
        self.download_item = download_item
        self.record = record or {}
        self.last_time = time.time()
        self.last_bytes = 0

        # --- 1. Create All UI Elements (NO LAYOUT) ---
        self.icon_label = QLabel()
        self.filename_label = QLabel()
        self.progress_bar = QProgressBar()
        self.status_label = QLabel()

        self.cancel_button = QPushButton("Cancel")
        self.open_button = QPushButton("Open File")
        self.open_folder_button = QPushButton("Open Folder")
        self.remove_button = QPushButton("Remove")
        self.remove_button_failed = QPushButton("Remove")

        # --- Create Button Containers ---
        self.active_button_widget = QWidget()
        active_layout = QHBoxLayout(self.active_button_widget)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.addWidget(self.cancel_button)

        self.completed_button_widget = QWidget()
        completed_layout = QHBoxLayout(self.completed_button_widget)
        completed_layout.setContentsMargins(0, 0, 0, 0)
        completed_layout.addWidget(self.remove_button)
        completed_layout.addWidget(self.open_button)
        completed_layout.addWidget(self.open_folder_button)

        self.failed_button_widget = QWidget()
        failed_layout = QHBoxLayout(self.failed_button_widget)
        failed_layout.setContentsMargins(0, 0, 0, 0)
        failed_layout.addWidget(self.remove_button_failed)

        # --- Create the Stacked Layout for buttons ---
        self.button_stack = QStackedLayout()
        self.button_stack.addWidget(self.active_button_widget)      # Index 0
        self.button_stack.addWidget(self.completed_button_widget)   # Index 1
        self.button_stack.addWidget(self.failed_button_widget)      # Index 2

        # This is the main widget that goes into the grid
        self.button_container = QWidget()
        self.button_container.setLayout(self.button_stack)

        # --- 2. Set Sizing Hints ---
        font_metrics = self.fontMetrics()
        self.icon_label.setFixedWidth(font_metrics.height())
        self.status_label.setMinimumWidth(font_metrics.horizontalAdvance("Completed (999.99 MB)") + 10)
        self.progress_bar.setMinimumWidth(100)
        self.progress_bar.setMaximumHeight(font_metrics.height() + 4) # Make pbar slimmer

        # --- 3. Connect Button Signals ---
        self.cancel_button.clicked.connect(self.cancel_download)
        self.open_button.clicked.connect(self.open_file)
        self.open_folder_button.clicked.connect(self.open_folder)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        self.remove_button_failed.clicked.connect(lambda: self.remove_requested.emit(self))

        # --- 4. Configure based on type (Active vs. Historic) ---
        if self.download_item:
            self.record = {
                "path": self.download_item.downloadFileName(),
                "url": self.download_item.url().toString(),
                "status": "Downloading",
                "total_bytes": initial_total_size
            }
            self.filename_label.setText(os.path.basename(self.record["path"]))
            self.download_item.receivedBytesChanged.connect(self.update_progress)
            self.download_item.stateChanged.connect(self.update_state)
            self.update_state(self.download_item.state())
        elif self.record:
            self.filename_label.setText(os.path.basename(self.record["path"]))
            self.update_ui_for_historic_state()

    def get_widgets_for_grid(self) -> list[QWidget]:
        """Returns all widgets to be placed in the grid layout."""
        return [self.icon_label, self.filename_label, self.progress_bar, self.status_label, self.button_container]

    def update_ui_for_historic_state(self):
        status = self.record.get("status", "Failed")
        self.progress_bar.show()

        if status == "Completed":
            self.progress_bar.setValue(100)
            size = self.record.get("total_bytes", 0)
            self.status_label.setText(f"Completed ({self.format_size(size)})")
            self.button_stack.setCurrentIndex(1) # Show completed buttons

            if not os.path.exists(self.record["path"]):
                self.status_label.setText("File not found")
                self.status_label.setStyleSheet("color: red; font-weight: bold;")
                self.open_button.setEnabled(False)
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
                self.icon_label.setPixmap(icon.pixmap(self.icon_label.sizeHint()))

        elif status in ("Cancelled", "Failed"):
            self.progress_bar.hide()
            self.status_label.setText(status)
            self.button_stack.setCurrentIndex(2) # Show failed buttons

    def cancel_download(self):
        if self.download_item:
            self.download_item.cancel()

    def open_file(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.record["path"]))

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.record["path"])))

    def format_size(self, nbytes: int) -> str:
        if nbytes >= 1024**3: return f"{nbytes / 1024**3:.2f} GB"
        if nbytes >= 1024**2: return f"{nbytes / 1024**2:.2f} MB"
        if nbytes >= 1024: return f"{nbytes / 1024:.2f} KB"
        return f"{nbytes} B"

    def update_progress(self):
        if not self.download_item:
            return
        received = self.download_item.receivedBytes()
        total = self.download_item.totalBytes()
        if total <= 0:
            total = self.record.get("total_bytes", 0)
        now = time.time()
        elapsed = now - self.last_time
        speed_str = ""
        if elapsed > 0.5:
            speed = (received - self.last_bytes) / elapsed
            speed_str = f"({self.format_size(int(speed))}/s)"
            self.last_time, self.last_bytes = now, received
        if total > 0:
            percent = int((received / total) * 100)
            self.progress_bar.setValue(min(percent, 100))
            self.status_label.setText(f"{self.format_size(received)} / {self.format_size(total)} {speed_str}")
        else:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText(f"{self.format_size(received)} / ??? {speed_str}")

    def update_state(self, state):
        if not self.download_item:
            return

        total_bytes = self.download_item.totalBytes()
        if total_bytes <= 0:
            total_bytes = self.record.get("total_bytes", 0)

        self.progress_bar.show()

        if state == QWebEngineDownloadRequest.DownloadState.DownloadInProgress:
            self.record["status"] = "Downloading"
            self.status_label.setText("Downloading...")
            self.button_stack.setCurrentIndex(0) # Show active (Cancel)
            self.update_progress()

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.record["status"] = "Completed"
            self.record["total_bytes"] = self.download_item.totalBytes()
            self.progress_bar.setValue(100)
            self.status_label.setText(f"Completed ({self.format_size(self.record['total_bytes'])})")
            self.button_stack.setCurrentIndex(1) # Show completed buttons
            self.finished.emit(self.record)

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self.record["status"] = "Cancelled"
            self.status_label.setText("Cancelled")
            self.progress_bar.hide()
            self.button_stack.setCurrentIndex(2) # Show failed (Remove)
            self.finished.emit(self.record)

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
            self.record["status"] = "Failed"
            self.status_label.setText("Failed")
            self.progress_bar.hide()
            self.button_stack.setCurrentIndex(2) # Show failed (Remove)
            self.finished.emit(self.record)


class DownloadManagerWidget(QWidget):

    def __init__(self, data_path: str, parent=None):
        super().__init__(parent)
        self.json_path = os.path.join(data_path, "downloads.json")
        self.download_records = []
        self.widget_map = {}

        self.main_layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()

        self.downloads_layout = QGridLayout(self.scroll_content)

        # Set proportional stretch factors for each column
        self.downloads_layout.setColumnStretch(1, 4) # Filename
        self.downloads_layout.setColumnStretch(2, 2) # Progress Bar
        self.downloads_layout.setColumnStretch(3, 2) # Status Label
        self.downloads_layout.setColumnStretch(4, 1) # Buttons

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

    def add_download(self, download_item, total_size: int = 0):
        controller = DownloadItemWidget(
            download_item=download_item,
            initial_total_size=total_size
        )

        controller.finished.connect(self.on_download_finished)
        controller.remove_requested.connect(self.remove_download_item)

        existing_controller = self.find_controller_by_url(download_item.url().toString())
        if existing_controller:
            self.remove_download_item(existing_controller)

        self.insert_row_at(0, controller)
        self.download_records.append(controller.record)

        self.save_history()

    def on_download_finished(self, record: dict):
        self.save_history()

    def load_history(self):
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    self.download_records = json.load(f)

                for record in reversed(self.download_records):
                    if record["status"] == "Downloading":
                        record["status"] = "Failed"

                    controller = DownloadItemWidget(record=record)
                    controller.remove_requested.connect(self.remove_download_item)
                    self.add_download_to_grid(controller)

            # Add a stretch row at the bottom to push all items to the top
            self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)

        except Exception as e:
            print(f"Error loading download history: {e}")
            self.download_records = []

    def save_history(self):
        try:
            self.download_records = [c.record for c in self.widget_map.keys()]
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
        self.downloads_layout.setRowStretch(self.downloads_layout.rowCount() - 1, 0)

        widgets_to_remove = self.widget_map.pop(controller)
        controller.deleteLater()

        row_to_remove = -1
        for i in range(self.downloads_layout.rowCount()):
            item = self.downloads_layout.itemAtPosition(i, 0)
            if item and item.widget() == widgets_to_remove[0]:
                row_to_remove = i
                break

        if row_to_remove == -1:
            # Item not found, re-add stretch and exit
            self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)
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
                    taken_item = self.downloads_layout.takeAt(self.downloads_layout.indexOf(item))
                    if taken_item:
                        self.downloads_layout.addItem(taken_item, row - 1, col)

        # Re-add the stretch row at the new bottom
        self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)

        self.save_history()

    def insert_row_at(self, row_index: int, controller: DownloadItemWidget):
        # Remove the old stretch row
        self.downloads_layout.setRowStretch(self.downloads_layout.rowCount() - 1, 0)

        # Shift all existing rows down by one
        for row in range(self.downloads_layout.rowCount() - 1, row_index - 1, -1):
            for col in range(self.downloads_layout.columnCount()):
                item = self.downloads_layout.itemAtPosition(row, col)
                if item:
                    taken_item = self.downloads_layout.takeAt(self.downloads_layout.indexOf(item))
                    if taken_item:
                        self.downloads_layout.addItem(taken_item, row + 1, col)

        # Add the new row
        widgets = controller.get_widgets_for_grid()
        self.downloads_layout.addWidget(widgets[0], row_index, 0)
        self.downloads_layout.addWidget(widgets[1], row_index, 1)
        self.downloads_layout.addWidget(widgets[2], row_index, 2)
        self.downloads_layout.addWidget(widgets[3], row_index, 3)
        self.downloads_layout.addWidget(widgets[4], row_index, 4)

        # Re-add the stretch row at the new bottom
        self.downloads_layout.setRowStretch(self.downloads_layout.rowCount(), 1)

        # Store controller and its widgets
        self.widget_map[controller] = widgets