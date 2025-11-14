import configparser
import glob
import json
import os
import sys
import time

from PyQt6.QtCore import pyqtSlot, QProcess, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWidgets import QDialog, QProgressDialog, QWidget, QProgressBar, QPushButton, QHBoxLayout, QLabel, QStyle, \
    QStackedLayout

from gameyfin_frontend.dialogs import SelectShortcutsDialog, InstallConfigDialog, SelectUmuIdDialog, \
    SelectLauncherDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.utils import get_xdg_user_dir
from gameyfin_frontend.workers import UnzipWorker, ProcessMonitorWorker


class DownloadItemWidget(QWidget):
    remove_requested = pyqtSignal(QWidget)
    finished = pyqtSignal(dict)

    def __init__(self, umu_database: UmuDatabase, download_item: QWebEngineDownloadRequest = None, record: dict = None,
                 initial_total_size: int = 0, parent=None):
        super().__init__(parent)
        self.umu_database = umu_database
        self.download_item = download_item
        self.record = record or {}
        self.last_time = time.time()
        self.last_bytes = 0

        self.thread = None
        self.worker = None
        self.progress_dialog = None
        self.current_install_config = None

        self.run_process = None
        self.current_wine_prefix = None

        self.monitor_thread = None
        self.monitor_worker = None

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

        self.button_stack = QStackedLayout()
        self.button_stack.addWidget(self.active_button_widget)
        self.button_stack.addWidget(self.completed_button_widget)
        self.button_stack.addWidget(self.failed_button_widget)

        self.button_container = QWidget()
        self.button_container.setLayout(self.button_stack)

        font_metrics = self.fontMetrics()
        self.icon_label.setFixedWidth(font_metrics.height())
        self.status_label.setMinimumWidth(font_metrics.horizontalAdvance("Completed (999.99 MB)") + 10)
        self.progress_bar.setMinimumWidth(100)
        self.progress_bar.setMaximumHeight(font_metrics.height() + 4)  # Make pbar slimmer

        self.cancel_button.clicked.connect(self.cancel_download)
        self.open_button.clicked.connect(self.open_file)
        self.open_folder_button.clicked.connect(self.open_folder)
        self.install_button.clicked.connect(self.install_package)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        self.remove_button_failed.clicked.connect(lambda: self.remove_requested.emit(self))

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
            self.button_stack.setCurrentIndex(1)
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
            self.button_stack.setCurrentIndex(2)
            self._update_install_button_visibility()

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

        target_dir = os.path.splitext(zip_path)[0]
        os.makedirs(target_dir, exist_ok=True)

        self.install_button.setEnabled(False)

        self.progress_dialog = QProgressDialog("Unzipping package...", "Cancel", 0, 100, self.parentWidget())
        self.progress_dialog.setWindowTitle("Installing")
        self.progress_dialog.setLabelText("Starting extraction...")
        self.progress_dialog.setModal(True)
        self.progress_dialog.canceled.connect(self.cancel_unzip)
        self.progress_dialog.show()

        self.thread = QThread()
        self.worker = UnzipWorker(zip_path, target_dir)
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self.progress_dialog.setValue)
        self.worker.current_file.connect(self.progress_dialog.setLabelText)
        self.worker.finished.connect(self.on_unzip_finished)
        self.worker.error.connect(self.on_unzip_error)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.worker.destroyed.connect(self.on_unzip_worker_deleted)
        self.thread.destroyed.connect(self.on_unzip_thread_deleted)

        self.thread.start()

    @pyqtSlot()
    def on_unzip_worker_deleted(self):
        print("Unzip worker cleaned up.")
        self.worker = None

    @pyqtSlot()
    def on_unzip_thread_deleted(self):
        print("Unzip thread cleaned up.")
        self.thread = None

    @pyqtSlot()
    def on_unzip_finished(self):
        """
        Called when the worker successfully finishes.
        Finds game metadata, gets user config, finds the launcher,
        and then delegates to a platform-specific installer method.
        """
        self.progress_dialog.close()
        self.install_button.setEnabled(True)

        target_dir = os.path.splitext(self.record["path"])[0]

        default_game_id = "umu-default"
        default_store = "none"
        results = []

        try:
            # Attempt 1: Search by product.json codename
            json_files = glob.glob(os.path.join(target_dir, "product_*.json"))
            if json_files:
                product_json_path = json_files[0]
                print(f"Found product info: {product_json_path}")

                with open(product_json_path, 'r') as f:
                    product_data = json.load(f)

                codename = product_data.get("id")

                if codename:
                    print(f"Found codename: {codename}")
                    results = self.umu_database.get_game_by_codename(str(codename))
                    print(f"API results (by codename): {results}")

            # Attempt 2: Fallback to search by zip name
            if not results:
                zip_name_base = os.path.basename(os.path.splitext(self.record["path"])[0])
                search_title = zip_name_base.replace('_', ' ').replace('-', ' ').strip()

                if search_title:
                    print(f"No results from codename. Fallback: searching by title: '{search_title}'")
                    results = self.umu_database.search_by_partial_title(search_title)
                    print(f"API results (by title): {results}")
                else:
                    print("No codename found and zip name was empty. Skipping UMU search.")

            selected_entry = None
            if isinstance(results, list) and len(results) > 0:
                if len(results) == 1:
                    selected_entry = results[0]
                    print("One matching entry found.")
                else:
                    print("Multiple matching entries found, showing dialog.")
                    umu_dialog = SelectUmuIdDialog(results, self)
                    if umu_dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_entry = umu_dialog.get_selected_entry()
                    else:
                        print("User cancelled UMU ID selection.")

                if selected_entry:
                    default_game_id = selected_entry.get("umu_id", default_game_id)
                    default_store = selected_entry.get("store", default_store)
                    print(f"Using: umu_id={default_game_id}, store={default_store}")

        except Exception as e:
            print(f"Error during UMU auto-detection: {e}")

        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self,
            default_game_id=default_game_id,
            default_store=default_store
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_label.setText("Install cancelled by user.")
            self.status_label.setStyleSheet("")
            return

        self.current_install_config = dialog.get_config()

        launcher_paths = []
        launcher_to_run = None

        try:
            for root, dirs, files in os.walk(target_dir):
                for file in files:
                    if file.lower().endswith(".exe"):
                        launcher_paths.append(os.path.join(root, file))
        except Exception as e:
            print(f"Error searching for launcher: {e}")
            self.on_unzip_error(f"Install OK, but failed to find launcher: {e}")
            return

        if not launcher_paths:
            self.status_label.setText("Install complete, no .exe found.")
            self.status_label.setStyleSheet("color: #E67E22;")
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))
        elif len(launcher_paths) == 1:
            launcher_to_run = launcher_paths[0]
        else:
            dialog = SelectLauncherDialog(target_dir, launcher_paths, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                launcher_to_run = dialog.get_selected_launcher()
                if not launcher_to_run:
                    self.status_label.setText("Install complete, no launcher selected.")
                    self.status_label.setStyleSheet("color: #E67E22;")
            else:
                self.status_label.setText("Install complete, launch cancelled.")
                self.status_label.setStyleSheet("")

        if launcher_to_run:
            if sys.platform == "linux":
                self._start_linux_installation(launcher_to_run, target_dir, self.current_install_config)
            else:
                raise NotImplementedError("Other platforms not yet implemented.")


    def _start_linux_installation(self, launcher_to_run: str, target_dir: str, install_config: dict):
        """
        Handles the Linux-specific install/run process using umu-run,
        Proton, and WINE prefixes.
        """
        try:
            config = install_config or {}

            home_dir = os.path.expanduser("~")
            folder_name = os.path.basename(target_dir)
            pfx_name = f"{folder_name.lower()}_pfx"
            wine_prefix_path = os.path.join(home_dir, ".config", "gameyfin", "prefixes", pfx_name)

            self.current_wine_prefix = wine_prefix_path

            self.run_process = QProcess()
            launcher_dir = os.path.dirname(launcher_to_run)
            self.run_process.setWorkingDirectory(launcher_dir)

            proton_path = os.getenv("PROTONPATH", "GE-Proton")
            env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{wine_prefix_path}\" "

            print("[Install] Applying user environment configuration:")
            for key, value in config.items():
                print(f"  {key}={value}")
                env_prefix += f"{key}=\"{value}\" "

            command_string = f"{env_prefix} umu-run \"{launcher_to_run}\""
            print(f"Executing command: /bin/sh -c \"{command_string}\"")

            success, pid = self.run_process.startDetached("/bin/sh", ["-c", command_string])

            if success and pid > 0:
                print(f"Launch successful. Monitoring PID: {pid}")
                size = self.record.get("total_bytes", 0)
                self.status_label.setText(f"Running... ({self.format_size(size)})")
                self.status_label.setStyleSheet("color: #3498DB;")

                self.monitor_thread = QThread()
                self.monitor_worker = ProcessMonitorWorker(pid)
                self.monitor_worker.moveToThread(self.monitor_thread)

                self.monitor_worker.finished.connect(self.on_run_finished)

                self.monitor_thread.started.connect(self.monitor_worker.run)
                self.monitor_worker.finished.connect(self.monitor_thread.quit)
                self.monitor_worker.finished.connect(self.monitor_worker.deleteLater)
                self.monitor_thread.finished.connect(self.monitor_thread.deleteLater)

                self.monitor_worker.destroyed.connect(self.on_monitor_worker_deleted)
                self.monitor_thread.destroyed.connect(self.on_monitor_thread_deleted)

                self.monitor_thread.start()

            else:
                print(f"Launch failed (startDetached returned {success}, PID: {pid}).")
                self.status_label.setText("Launch failed. Is 'umu-run' installed?")
                self.status_label.setStyleSheet("color: red;")
                self.current_wine_prefix = None

        except Exception as e:
            self.status_label.setText(f"Launch failed: {e}")
            self.status_label.setStyleSheet("color: red;")
            self.current_wine_prefix = None

    @pyqtSlot(str)
    def on_unzip_error(self, message: str):
        """Called when the worker reports an error."""
        if self.progress_dialog:
            self.progress_dialog.close()
        self.install_button.setEnabled(True)
        self.status_label.setText(f"Install failed: {message}")
        self.status_label.setStyleSheet("color: red;")

        self.current_install_config = None

    @pyqtSlot()
    def cancel_unzip(self):
        """Called when the 'Cancel' button on the dialog is clicked."""
        if self.worker:
            self.worker.stop()
        if self.thread:
            self.thread.quit()

        if self.monitor_worker:
            self.monitor_worker.stop()
        if self.monitor_thread:
            self.monitor_thread.quit()

        self.status_label.setText("Install cancelled")
        self.install_button.setEnabled(True)

        self.current_install_config = None

        if self.run_process:
            self.run_process.deleteLater()
            self.run_process = None

    @pyqtSlot()
    def on_monitor_worker_deleted(self):
        print("Process monitor worker cleaned up.")
        self.monitor_worker = None

    @pyqtSlot()
    def on_monitor_thread_deleted(self):
        print("Process monitor thread cleaned up.")
        self.monitor_thread = None

    @pyqtSlot()
    def on_run_finished(self):
        """
        Called when the ProcessMonitorWorker signals that the PID has finished.
        """
        print(f"umu-run process finished.")

        if self.current_wine_prefix and os.path.isdir(self.current_wine_prefix):
            shortcuts_dir = os.path.join(self.current_wine_prefix, "drive_c", "proton_shortcuts")
            print(f"Checking for shortcuts in: {shortcuts_dir}")

            if os.path.isdir(shortcuts_dir):
                desktop_files = glob.glob(os.path.join(shortcuts_dir, "*.desktop"))

                if desktop_files:
                    print(f"Found {len(desktop_files)} potential .desktop files.")

                    dialog = SelectShortcutsDialog(desktop_files, self.parentWidget())
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_files = dialog.get_selected_files()

                        if selected_files:
                            print(f"User selected {len(selected_files)} shortcuts to create. Processing...")
                            self.create_desktop_shortcuts(selected_files)
                        else:
                            print("User selected no shortcuts.")
                    else:
                        print("User cancelled shortcut creation.")

                else:
                    print("No .desktop files found in proton_shortcuts.")
            else:
                print("proton_shortcuts directory does not exist.")
        else:
            print("WINEPREFIX path not set or does not exist, skipping shortcut check.")

        size = self.record.get("total_bytes", 0)
        self.status_label.setText(f"Completed ({self.format_size(size)})")
        self.status_label.setStyleSheet("")

        self.current_install_config = None
        self.current_wine_prefix = None

    def create_desktop_shortcuts(self, desktop_files: list):
        """
        Parses the found .desktop files, creates a helper .sh script for
        the launch command, and saves the .desktop file to the user's
        Desktop and applications directories.
        """
        if not self.current_install_config:
            print("Error: Install config was cleared too early. Cannot create shortcuts.")
            self.current_install_config = {}

        home_dir = os.path.expanduser("~")

        prefix_basename = os.path.basename(self.current_wine_prefix)
        game_name = prefix_basename.removesuffix("_pfx")
        if not game_name:
            game_name = "unknown-game"

        shortcut_scripts_path = os.path.join(home_dir,
                                             ".config",
                                             "gameyfin",
                                             "shortcut_scripts",
                                             str(game_name))
        os.makedirs(shortcut_scripts_path, exist_ok=True)

        dirs = list()
        desktop_dir = os.path.join(home_dir, get_xdg_user_dir("DESKTOP"))
        applications_dir = os.path.join(home_dir, ".local/share/applications")
        dirs.append(applications_dir)
        dirs.append(desktop_dir)

        for dir in dirs:
            os.makedirs(dir, exist_ok=True)

            for original_path in desktop_files:
                try:
                    print(f"Processing: {os.path.basename(original_path)}")

                    # configparser needs a section header
                    with open(original_path, 'r') as f:
                        content = f.read()
                    if not content.strip().startswith('[Desktop Entry]'):
                        content = '[Desktop Entry]\n' + content

                    # Use strict=False to allow duplicate keys
                    config_parser = configparser.ConfigParser(strict=False)

                    # Tell configparser to preserve key case (e.g., 'Type' not 'type')
                    config_parser.optionxform = str

                    config_parser.read_string(content)

                    if 'Desktop Entry' not in config_parser:
                        print(f"Skipping {os.path.basename(original_path)}: Cannot find [Desktop Entry] section.")
                        continue

                    entry = config_parser['Desktop Entry']

                    icon_name = entry.get('Icon')
                    if icon_name:
                        shortcuts_dir = os.path.dirname(original_path)
                        icons_base_dir = os.path.join(shortcuts_dir, "icons")

                        sizes_to_check = ["256x256", "128x128", "64x64", "48x48", "32x32"]
                        found_icon_path = None

                        for size in sizes_to_check:
                            path_with_png = os.path.join(icons_base_dir, size, "apps", f"{icon_name}.png")
                            path_as_is = os.path.join(icons_base_dir, size, "apps", icon_name)

                            if os.path.exists(path_with_png):
                                found_icon_path = path_with_png
                                break
                            elif os.path.exists(path_as_is):
                                found_icon_path = path_as_is
                                break

                        if found_icon_path:
                            config_parser.set('Desktop Entry', 'Icon', found_icon_path)
                            print(f"Updated icon path to: {found_icon_path}")
                        else:
                            print(f"Warning: Could not find icon '{icon_name}' in {icons_base_dir}")

                    working_dir = entry.get('Path')
                    exe_name = entry.get('StartupWMClass')
                    if not exe_name:
                        exe_name = entry.get('Name', 'game') + ".exe"
                        print(f"Warning: No StartupWMClass. Guessing exe name: {exe_name}")

                    if not working_dir:
                        print(f"Skipping {os.path.basename(original_path)}: No 'Path' entry found.")
                        continue

                    exe_path = os.path.join(working_dir, exe_name)
                    config = self.current_install_config or {}

                    proton_path = os.getenv("PROTONPATH", "GE-Proton")
                    env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{self.current_wine_prefix}\" "

                    for key, value in config.items():
                        env_prefix += f"{key}=\"{value}\" "

                    command_to_run = f"{env_prefix} umu-run \"{exe_path}\""

                    script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
                    script_path = os.path.join(shortcut_scripts_path, script_name)
                    script_content = f"#!/bin/sh\n\n# Auto-generated by Gameyfin\n{command_to_run}\n"

                    with open(script_path, 'w') as f:
                        f.write(script_content)

                    os.chmod(script_path, 0o755)
                    print(f"Created helper script at: {script_path}")

                    config_parser.set('Desktop Entry', 'Exec', f'"{script_path}"')

                    config_parser.set('Desktop Entry', 'Type', 'Application')
                    config_parser.set('Desktop Entry', 'Categories', 'Application;Game;')

                    new_file_name = os.path.basename(original_path)
                    new_file_path = os.path.join(dir, new_file_name)

                    with open(new_file_path, 'w') as f:
                        config_parser.write(f)

                    os.chmod(new_file_path, 0o755)

                    print(f"Successfully created shortcut at: {new_file_path}")

                except Exception as e:
                    print(f"Failed to process shortcut {original_path}: {e}")

    @staticmethod
    def format_size(nbytes: int) -> str:
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
            self.button_stack.setCurrentIndex(0)
            self.update_progress()

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.record["status"] = "Completed"
            self.record["total_bytes"] = total_bytes
            self.progress_bar.setValue(100)
            self.status_label.setText(f"Completed ({self.format_size(self.record['total_bytes'])})")
            self.button_stack.setCurrentIndex(1)
            self._update_install_button_visibility()
            self.finished.emit(self.record)

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self.record["status"] = "Cancelled"
            self.status_label.setText("Cancelled")
            self.progress_bar.hide()
            self.button_stack.setCurrentIndex(2)
            self.finished.emit(self.record)

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
            self.record["status"] = "Failed"
            self.status_label.setText("Failed")
            self.progress_bar.hide()
            self.button_stack.setCurrentIndex(2)
            self.finished.emit(self.record)