import json
import os
import time
import zipfile

from PyQt6.QtCore import QUrl, pyqtSignal, QObject, QThread, pyqtSlot, QProcess, QProcessEnvironment
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWidgets import (QGridLayout, QWidget, QScrollArea, QVBoxLayout, QStyle,
                             QStackedLayout, QHBoxLayout, QPushButton, QLabel,
                             QProgressBar, QProgressDialog, QDialog, QFormLayout,
                             QLineEdit, QComboBox, QCheckBox, QPlainTextEdit,
                             QDialogButtonBox, QListWidget)  # Added QListWidget


class UnzipWorker(QObject):
    """
    Runs the zip extraction in a separate thread to avoid freezing the UI.
    """
    # Signals
    progress = pyqtSignal(int)  # Current percentage (0-100)
    current_file = pyqtSignal(str)  # Name of file being extracted
    finished = pyqtSignal()  # Emitted on success
    error = pyqtSignal(str)  # Emitted on failure

    def __init__(self, zip_path: str, target_dir: str):
        super().__init__()
        self.zip_path = zip_path
        self.target_dir = target_dir
        self._is_running = True

    @pyqtSlot()
    def run(self):
        """Starts the extraction process."""
        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                file_list = zip_ref.infolist()
                total_files = len(file_list)

                if total_files == 0:
                    self.finished.emit()
                    return

                for i, member in enumerate(file_list):
                    if not self._is_running:
                        self.error.emit("Extraction cancelled by user.")
                        return

                    # Extract the single file
                    zip_ref.extract(member, path=self.target_dir)

                    # Calculate and emit progress
                    percentage = int(((i + 1) / total_files) * 100)
                    self.progress.emit(percentage)
                    self.current_file.emit(f"Extracting: {member.filename}")

                self.finished.emit()

        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        """Flags the worker to stop."""
        self._is_running = False


class InstallConfigDialog(QDialog):
    """
    A dialog to configure environment variables before installation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Installation Configuration")
        self.setMinimumWidth(400)

        # --- Layouts ---
        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # --- Widgets ---
        self.wayland_checkbox = QCheckBox("Enable Wayland support (PROTON_ENABLE_WAYLAND)")

        self.gameid_input = QLineEdit()
        self.gameid_input.setText("umu-default")

        self.store_combo = QComboBox()
        stores = ["none", "gog", "amazon", "battlenet", "ea", "egs",
                  "humble", "itchio", "steam", "ubisoft", "zoomplatform"]
        self.store_combo.addItems(stores)

        self.extra_vars_input = QPlainTextEdit()
        self.extra_vars_input.setPlaceholderText("KEY1=VALUE1\nKEY2=VALUE2")

        # --- Button Box ---
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        # --- Assemble Layout ---
        main_layout.addWidget(self.wayland_checkbox)

        form_layout.addRow("Umu protonfix:", self.gameid_input)
        form_layout.addRow("Store:", self.store_combo)
        main_layout.addLayout(form_layout)

        main_layout.addWidget(QLabel("Additional Environment Variables (one per line):"))
        main_layout.addWidget(self.extra_vars_input)

        main_layout.addWidget(button_box)

    def get_config(self) -> dict:
        """
        Returns the configured environment variables as a dictionary.
        """
        config = {"PROTON_ENABLE_WAYLAND": "1" if self.wayland_checkbox.isChecked() else "0"}

        # 1. Wayland

        # 2. Game ID
        game_id = self.gameid_input.text().strip()
        if game_id:
            config["GAMEID"] = game_id

        # 3. Store
        store = self.store_combo.currentText()
        if store and store != "none":  # no store is default
            config["STORE"] = store

        # 4. Extra Vars
        extra_vars_text = self.extra_vars_input.toPlainText().strip()
        if extra_vars_text:
            for line in extra_vars_text.splitlines():
                if "=" in line:
                    parts = line.split("=", 1)
                    key = parts[0].strip()
                    value = parts[1].strip()
                    if key:
                        config[key] = value

        return config


class SelectLauncherDialog(QDialog):
    """
    A dialog to select an executable when multiple are found.
    """

    def __init__(self, target_dir: str, exe_paths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Launcher")
        self.setMinimumWidth(450)
        self.exe_map = {}  # Maps relative path (display) to full path (actual)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(QLabel("Multiple executables found. Please select one to launch:"))

        self.list_widget = QListWidget()
        for full_path in exe_paths:
            # Create a relative path for display
            relative_path = os.path.relpath(full_path, target_dir)
            self.exe_map[relative_path] = full_path
            self.list_widget.addItem(relative_path)

        main_layout.addWidget(self.list_widget)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)

        self.ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)  # Disable OK until one is selected

        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        main_layout.addWidget(button_box)

    def on_selection_changed(self, current_item, previous_item):
        """Enables the OK button when an item is selected."""
        self.ok_button.setEnabled(current_item is not None)

    def get_selected_launcher(self) -> str | None:
        """Returns the full path of the selected executable."""
        item = self.list_widget.currentItem()
        if not item:
            return None

        relative_path = item.text()
        return self.exe_map.get(relative_path)


class DownloadItemWidget(QWidget):
    remove_requested = pyqtSignal(QWidget)
    finished = pyqtSignal(dict)

    def __init__(self, download_item: QWebEngineDownloadRequest = None, record: dict = None,
                 initial_total_size: int = 0, parent=None):
        super().__init__(parent)
        self.download_item = download_item
        self.record = record or {}
        self.last_time = time.time()
        self.last_bytes = 0

        self.thread = None
        self.worker = None
        self.progress_dialog = None
        self.current_install_config = None

        self.icon_label = QLabel()
        self.filename_label = QLabel()
        self.progress_bar = QProgressBar()
        self.status_label = QLabel()

        self.cancel_button = QPushButton("Cancel")
        self.open_button = QPushButton("Open File")
        self.open_folder_button = QPushButton("Open Folder")
        self.remove_button = QPushButton("Remove")
        self.remove_button_failed = QPushButton("Remove")
        self.install_button = QPushButton("Install")

        # --- Create Button Containers ---
        self.active_button_widget = QWidget()
        active_layout = QHBoxLayout(self.active_button_widget)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.addWidget(self.cancel_button)

        self.completed_button_widget = QWidget()
        completed_layout = QHBoxLayout(self.completed_button_widget)
        completed_layout.setContentsMargins(0, 0, 0, 0)
        completed_layout.addWidget(self.remove_button)
        completed_layout.addWidget(self.install_button)
        completed_layout.addWidget(self.open_button)
        completed_layout.addWidget(self.open_folder_button)

        self.failed_button_widget = QWidget()
        failed_layout = QHBoxLayout(self.failed_button_widget)
        failed_layout.setContentsMargins(0, 0, 0, 0)
        failed_layout.addWidget(self.remove_button_failed)

        # --- Create the Stacked Layout for buttons ---
        self.button_stack = QStackedLayout()
        self.button_stack.addWidget(self.active_button_widget)  # Index 0
        self.button_stack.addWidget(self.completed_button_widget)  # Index 1
        self.button_stack.addWidget(self.failed_button_widget)  # Index 2

        # This is the main widget that goes into the grid
        self.button_container = QWidget()
        self.button_container.setLayout(self.button_stack)

        # --- 2. Set Sizing Hints ---
        font_metrics = self.fontMetrics()
        self.icon_label.setFixedWidth(font_metrics.height())
        self.status_label.setMinimumWidth(font_metrics.horizontalAdvance("Completed (999.99 MB)") + 10)
        self.progress_bar.setMinimumWidth(100)
        self.progress_bar.setMaximumHeight(font_metrics.height() + 4)  # Make pbar slimmer

        # --- 3. Connect Button Signals ---
        self.cancel_button.clicked.connect(self.cancel_download)
        self.open_button.clicked.connect(self.open_file)
        self.open_folder_button.clicked.connect(self.open_folder)
        self.install_button.clicked.connect(self.install_package)
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

        self._update_install_button_visibility()

    def get_widgets_for_grid(self) -> list[QWidget]:
        """Returns all widgets to be placed in the grid layout."""
        return [self.icon_label, self.filename_label, self.progress_bar, self.status_label, self.button_container]

    def _update_install_button_visibility(self):
        """Hides or shows the install button based on file type."""
        path = self.record.get("path", "")
        is_zip = path.lower().endswith(".zip")
        self.install_button.setVisible(is_zip)

    def update_ui_for_historic_state(self):
        status = self.record.get("status", "Failed")
        self.progress_bar.show()

        if status == "Completed":
            self.progress_bar.setValue(100)
            size = self.record.get("total_bytes", 0)
            self.status_label.setText(f"Completed ({self.format_size(size)})")
            self.button_stack.setCurrentIndex(1)  # Show completed buttons
            self._update_install_button_visibility()

            if not os.path.exists(self.record["path"]):
                self.status_label.setText("File not found")
                self.status_label.setStyleSheet("color: red; font-weight: bold;")
                self.open_button.setEnabled(False)
                self.install_button.setEnabled(False)
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
                self.icon_label.setPixmap(icon.pixmap(self.icon_label.sizeHint()))

        elif status in ("Cancelled", "Failed"):
            self.progress_bar.hide()
            self.status_label.setText(status)
            self.button_stack.setCurrentIndex(2)  # Show failed buttons
            self._update_install_button_visibility()  # Hide for failed

    def cancel_download(self):
        if self.download_item:
            self.download_item.cancel()

    def open_file(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.record["path"]))

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.record["path"])))

    def install_package(self):
        """
        Shows config dialog, then sets up the QProgressDialog
        and starts the UnzipWorker in a new thread.
        """
        zip_path = self.record.get("path")
        if not zip_path or not zip_path.lower().endswith(".zip") or not os.path.exists(zip_path):
            self.status_label.setText("Install failed: File not found")
            self.status_label.setStyleSheet("color: red;")
            return

        # --- Show Configuration Dialog ---
        dialog = InstallConfigDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_label.setText("Install cancelled by user.")
            self.status_label.setStyleSheet("")
            return  # User cancelled

        # Store the config for on_unzip_finished
        self.current_install_config = dialog.get_config()
        # --- End Dialog Logic ---

        # Create target directory (e.g., 'C:/downloads/my-file.zip' -> 'C:/downloads/my-file')
        target_dir = os.path.splitext(zip_path)[0]
        os.makedirs(target_dir, exist_ok=True)

        self.install_button.setEnabled(False)  # Disable button during install

        # --- 1. Set up the Progress Dialog ---
        self.progress_dialog = QProgressDialog("Unzipping package...", "Cancel", 0, 100, self.parentWidget())
        self.progress_dialog.setWindowTitle("Installing")
        self.progress_dialog.setLabelText("Starting extraction...")
        self.progress_dialog.setModal(True)
        self.progress_dialog.canceled.connect(self.cancel_unzip)  # Connect cancel button
        self.progress_dialog.show()

        # --- 2. Set up the Thread and Worker ---
        self.thread = QThread()
        self.worker = UnzipWorker(zip_path, target_dir)
        self.worker.moveToThread(self.thread)

        # --- 3. Connect signals from worker to UI ---
        self.worker.progress.connect(self.progress_dialog.setValue)
        self.worker.current_file.connect(self.progress_dialog.setLabelText)
        self.worker.finished.connect(self.on_unzip_finished)
        self.worker.error.connect(self.on_unzip_error)

        # --- 4. Connect thread management signals ---
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # --- 5. Start the thread ---
        self.thread.start()

    @pyqtSlot()
    def on_unzip_finished(self):
        """
        Called when the worker successfully finishes.
        Searches for .exe files, prompts user if > 1, then runs with 'umu-run'.
        """
        # --- Get the config from the dialog ---
        config = self.current_install_config or {}
        # ---

        self.progress_dialog.close()
        self.install_button.setEnabled(True)

        target_dir = os.path.splitext(self.record["path"])[0]
        launcher_paths = []
        launcher_to_run = None

        # --- Search for ALL .exe launchers ---
        try:
            for root, dirs, files in os.walk(target_dir):
                for file in files:
                    if file.lower().endswith(".exe"):
                        launcher_paths.append(os.path.join(root, file))
        except Exception as e:
            print(f"Error searching for launcher: {e}")
            self.on_unzip_error(f"Install OK, but failed to find launcher: {e}")
            return
        # --- END SEARCH ---

        # --- Decide which launcher to run (if any) ---
        if not launcher_paths:
            # Case 1: No .exe found
            self.status_label.setText("Install complete, no .exe found.")
            self.status_label.setStyleSheet("color: #E67E22;")
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))

        elif len(launcher_paths) == 1:
            # Case 2: Exactly one .exe found
            launcher_to_run = launcher_paths[0]

        else:
            # Case 3: Multiple .exe found
            dialog = SelectLauncherDialog(target_dir, launcher_paths, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                launcher_to_run = dialog.get_selected_launcher()
                if not launcher_to_run:
                    self.status_label.setText("Install complete, no launcher selected.")
                    self.status_label.setStyleSheet("color: #E67E22;")
            else:
                # User cancelled
                self.status_label.setText("Install complete, launch cancelled.")
                self.status_label.setStyleSheet("")

        # --- Launch the selected .exe (if one was selected) ---
        if launcher_to_run:
            try:
                # 1. Get user's home directory
                home_dir = os.path.expanduser("~")

                # 2. Get lower-case folder name
                folder_name = os.path.basename(target_dir)
                pfx_name = f"{folder_name.lower()}_pfx"

                # 3. Construct WINEPREFIX
                wine_prefix_path = os.path.join(home_dir, ".config", "gameyfin", "prefixes", pfx_name)

                # 4. Create QProcess and set environment
                process = QProcess()
                env = QProcessEnvironment.systemEnvironment()  # Get current env

                # --- Apply base environment ---
                env.insert("PROTONPATH", os.getenv("PROTONPATH", "GE-Proton"))
                env.insert("WINEPREFIX", wine_prefix_path)

                # --- Apply user config from dialog ---
                print("[Install] Applying user environment configuration:")
                for key, value in config.items():
                    print(f"  {key}={value}")
                    env.insert(key, value)
                # --- End applying config ---

                process.setProcessEnvironment(env)
                launcher_dir = os.path.dirname(launcher_to_run)
                process.setWorkingDirectory(launcher_dir)  # Set working directory

                # --- 5. Set program/args on instance ---
                process.setProgram("umu-run")
                process.setArguments([launcher_to_run])

                # --- 6. Launch detached ---
                success = process.startDetached()

                if success:
                    size = self.record.get("total_bytes", 0)
                    self.status_label.setText(f"Completed ({self.format_size(size)})")
                    self.status_label.setStyleSheet("")
                else:
                    self.status_label.setText("Launch failed. Is 'umu-run' installed?")
                    self.status_label.setStyleSheet("color: red;")
                    QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))

            except Exception as e:
                self.status_label.setText(f"Launch failed: {e}")
                self.status_label.setStyleSheet("color: red;")
                QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))

        # --- Final Cleanup ---
        self.current_install_config = None  # Clean up config
        self.thread = None
        self.worker = None

    @pyqtSlot(str)
    def on_unzip_error(self, message: str):
        """Called when the worker reports an error."""
        if self.progress_dialog:
            self.progress_dialog.close()
        self.install_button.setEnabled(True)
        self.status_label.setText(f"Install failed: {message}")
        self.status_label.setStyleSheet("color: red;")

        self.current_install_config = None  # Clean up
        self.thread = None
        self.worker = None

    @pyqtSlot()
    def cancel_unzip(self):
        """Called when the 'Cancel' button on the dialog is clicked."""
        if self.worker:
            self.worker.stop()  # Tell worker to stop
        if self.thread:
            self.thread.quit()  # Ask thread to quit
            self.thread.wait(500)  # Wait 500ms

        self.status_label.setText("Install cancelled")
        self.install_button.setEnabled(True)

        self.current_install_config = None  # Clean up
        self.thread = None
        self.worker = None

    # --- END OF NEW/UPDATED METHODS ---

    def format_size(self, nbytes: int) -> str:
        if nbytes >= 1024 ** 3: return f"{nbytes / 1024 ** 3:.2f} GB"
        if nbytes >= 1024 ** 2: return f"{nbytes / 1024 ** 2:.2f} MB"
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
        self._update_install_button_visibility()

        if state == QWebEngineDownloadRequest.DownloadState.DownloadInProgress:
            self.record["status"] = "Downloading"
            self.status_label.setText("Downloading...")
            self.button_stack.setCurrentIndex(0)  # Show active (Cancel)
            self.update_progress()

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.record["status"] = "Completed"
            self.record["total_bytes"] = self.download_item.totalBytes()
            self.progress_bar.setValue(100)
            self.status_label.setText(f"Completed ({self.format_size(self.record['total_bytes'])})")
            self.button_stack.setCurrentIndex(1)  # Show completed buttons
            self._update_install_button_visibility()
            self.finished.emit(self.record)

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self.record["status"] = "Cancelled"
            self.status_label.setText("Cancelled")
            self.progress_bar.hide()
            self.button_stack.setCurrentIndex(2)  # Show failed (Remove)
            self.finished.emit(self.record)

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
            self.record["status"] = "Failed"
            self.status_label.setText("Failed")
            self.progress_bar.hide()
            self.button_stack.setCurrentIndex(2)  # Show failed (Remove)
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
        self.downloads_layout.setColumnStretch(1, 4)  # Filename
        self.downloads_layout.setColumnStretch(2, 2)  # Progress Bar
        self.downloads_layout.setColumnStretch(3, 2)  # Status Label
        self.downloads_layout.setColumnStretch(4, 1)  # Buttons

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