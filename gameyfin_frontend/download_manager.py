import glob
import json
import os
import time
import configparser  # Import configparser
from pathlib import Path

from PyQt6.QtCore import (QUrl, pyqtSignal, QThread, pyqtSlot, QProcess,
                          QProcessEnvironment)
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWidgets import (QGridLayout, QWidget, QScrollArea, QVBoxLayout, QStyle,
                             QStackedLayout, QHBoxLayout, QPushButton, QLabel,
                             QProgressBar, QProgressDialog, QDialog)

from gameyfin_frontend.dialogs import SelectUmuIdDialog, InstallConfigDialog, SelectLauncherDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.workers import UnzipWorker


def get_xdg_user_dir(dir_name: str) -> Path:
    """
    Finds a special XDG user directory (like DESKTOP, DOCUMENTS)
    in a language-independent way on Linux by reading the
    ~/.config/user-dirs.dirs file.

    Args:
        dir_name: The internal name of the directory (e.g., "DESKTOP",
                  "DOCUMENTS", "DOWNLOAD").
    """

    # 1. The key we are looking for in the file
    key_to_find = f"XDG_{dir_name.upper()}_DIR"

    # 2. Determine the config file path
    # It's almost always in ~/.config/user-dirs.dirs
    config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    config_file_path = Path(config_home) / "user-dirs.dirs"

    # 3. Set a sensible fallback (e.g., $HOME/Desktop)
    # This is used if the file or key doesn't exist
    fallback_dir = Path.home() / dir_name.capitalize()

    if not config_file_path.is_file():
        return fallback_dir

    try:
        with open(config_file_path, "r") as f:
            for line in f:
                line = line.strip()

                # Skip comments or empty lines
                if not line or line.startswith("#"):
                    continue

                # Check if this is the line we want
                if line.startswith(key_to_find):
                    try:
                        # Line looks like: XDG_DESKTOP_DIR="$HOME/Bureaublad"
                        # Split at '=', get the second part
                        value = line.split("=", 1)[1]

                        # Remove surrounding quotes (e.g., "...")
                        value = value.strip('"')

                        # IMPORTANT: Expand variables like $HOME
                        path = os.path.expandvars(value)

                        return Path(path)

                    except Exception:
                        # Found the key but the line was malformed, use fallback
                        return fallback_dir

    except Exception as e:
        print(f"Error reading {config_file_path}: {e}")
        # Fallback in case of permissions errors, etc.
        return fallback_dir

    # If the key (e.g., XDG_DESKTOP_DIR) wasn't found in the file
    return fallback_dir

class ProcessMonitorWorker(QThread):
    """Monitors a process by its PID and emits when it's finished."""
    finished = pyqtSignal()

    def __init__(self, pid, parent=None):
        super().__init__(parent)
        self.pid = pid
        self._running = True

    def run(self):
        if not self.pid > 0:
            print(f"ProcessMonitor: Invalid PID ({self.pid}), stopping.")
            return

        print(f"ProcessMonitor: Monitoring PID {self.pid}")
        self._running = True
        while self._running:
            try:
                # os.kill(pid, 0) checks if process exists
                # (on Unix-like systems).
                os.kill(self.pid, 0)
            except OSError:
                # Process does not exist
                print(f"ProcessMonitor: PID {self.pid} finished.")
                self._running = False
                self.finished.emit()
                break  # Exit loop
            else:
                # Process exists, sleep and check again
                if not self._running:
                    break  # Stop requested
                self.msleep(1000)  # Check every second

        print(f"ProcessMonitor: Stopping monitor for {self.pid}")

    def stop(self):
        self._running = False


# noinspection PyUnresolvedReferences
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

        self.run_process = None  # Holds the QProcess for setup
        self.current_wine_prefix = None  # Stores the WINEPREFIX path

        self.monitor_thread = None  # Thread for monitoring the game PID
        self.monitor_worker = None  # Worker for monitoring the game PID

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

        # --- 4b. Connect destroyed signals for safe cleanup ---
        self.worker.destroyed.connect(self.on_unzip_worker_deleted)
        self.thread.destroyed.connect(self.on_unzip_thread_deleted)

        # --- 5. Start the thread ---
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
        Searches for .exe files, prompts user if > 1, then runs with 'umu-run'.
        """
        self.progress_dialog.close()
        self.install_button.setEnabled(True)

        target_dir = os.path.splitext(self.record["path"])[0]

        default_game_id = "umu-default"
        default_store = "none"
        results = []

        try:
            # --- ATTEMPT 1: Search by product.json codename ---
            json_files = glob.glob(os.path.join(target_dir, "product_*.json"))
            if json_files:
                product_json_path = json_files[0]  # Use the first one found
                print(f"Found product info: {product_json_path}")

                # Parse JSON and get ID
                with open(product_json_path, 'r') as f:
                    product_data = json.load(f)

                codename = product_data.get("id")

                if codename:
                    print(f"Found codename: {codename}")
                    # Call UMU API
                    results = self.umu_database.get_game_by_codename(str(codename))
                    print(f"API results (by codename): {results}")

            # --- ATTEMPT 2: Fallback to search by zip name ---
            if not results:  # This runs if no json, no codename, or codename search failed
                # Get base name of zip, e.g., /path/to/my_game.zip -> my_game
                zip_name_base = os.path.basename(os.path.splitext(self.record["path"])[0])
                # Clean it up to be a better search term, e.g., "my_game" -> "my game"
                search_title = zip_name_base.replace('_', ' ').replace('-', ' ').strip()

                if search_title:
                    print(f"No results from codename. Fallback: searching by title: '{search_title}'")
                    # Call UMU API
                    results = self.umu_database.search_by_partial_title(search_title)
                    print(f"API results (by title): {results}")
                else:
                    print("No codename found and zip name was empty. Skipping UMU search.")

            selected_entry = None
            if isinstance(results, list) and len(results) > 0:
                if len(results) == 1:
                    # 1 result
                    selected_entry = results[0]
                    print("One matching entry found.")
                else:
                    # Multiple results
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
            # Non-fatal error, just proceed with defaults

        dialog = InstallConfigDialog(
            self,
            default_game_id=default_game_id,
            default_store=default_store
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_label.setText("Install cancelled by user.")
            self.status_label.setStyleSheet("")
            # Unzip thread/worker will be cleaned up by their signals
            return  # User cancelled

        # Store the config for launch
        self.current_install_config = dialog.get_config()

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
            # Unzip thread/worker will be cleaned up by their signals
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
                # --- Get the config from the dialog ---
                config = self.current_install_config or {}
                # ---

                # 1. Get user's home directory
                home_dir = os.path.expanduser("~")

                # 2. Get lower-case folder name
                folder_name = os.path.basename(target_dir)
                pfx_name = f"{folder_name.lower()}_pfx"

                # 3. Construct WINEPREFIX
                wine_prefix_path = os.path.join(home_dir, ".config", "gameyfin", "prefixes", pfx_name)

                # --- Store WINEPREFIX for the 'finished' slot ---
                self.current_wine_prefix = wine_prefix_path

                # 4. Create QProcess
                self.run_process = QProcess()  # Use the instance variable
                launcher_dir = os.path.dirname(launcher_to_run)
                self.run_process.setWorkingDirectory(launcher_dir)  # Set working directory

                # --- 5. Build the command string (MOST ROBUST WAY) ---
                # This injects the env vars directly into the shell command

                # --- Apply base environment ---
                proton_path = os.getenv("PROTONPATH", "GE-Proton")
                # We must quote paths in case they have spaces
                env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{wine_prefix_path}\" "

                # --- Apply user config from dialog ---
                print("[Install] Applying user environment configuration:")
                for key, value in config.items():
                    print(f"  {key}={value}")
                    # Add to the prefix, quoting the value
                    env_prefix += f"{key}=\"{value}\" "

                # --- Build full command ---
                # The environment prefix goes BEFORE the command
                # We must quote the launcher path
                command_string = f"{env_prefix} umu-run \"{launcher_to_run}\""
                print(f"Executing command: /bin/sh -c \"{command_string}\"")

                # --- 6. Launch using startDetached to get PID ---
                success, pid = self.run_process.startDetached("/bin/sh", ["-c", command_string])

                # --- 7. Check success and start monitor ---
                if success and pid > 0:
                    print(f"Launch successful. Monitoring PID: {pid}")
                    size = self.record.get("total_bytes", 0)
                    self.status_label.setText(f"Running... ({self.format_size(size)})")
                    self.status_label.setStyleSheet("color: #3498DB;")

                    # --- 8. Set up PID monitor ---
                    self.monitor_thread = QThread()
                    self.monitor_worker = ProcessMonitorWorker(pid)
                    self.monitor_worker.moveToThread(self.monitor_thread)

                    # Connect worker finish to our handler
                    self.monitor_worker.finished.connect(self.on_run_finished)

                    # Connect thread management
                    self.monitor_thread.started.connect(self.monitor_worker.run)
                    self.monitor_worker.finished.connect(self.monitor_thread.quit)
                    self.monitor_worker.finished.connect(self.monitor_worker.deleteLater)
                    self.monitor_thread.finished.connect(self.monitor_thread.deleteLater)

                    # --- 8b. Connect destroyed signals for safe cleanup ---
                    self.monitor_worker.destroyed.connect(self.on_monitor_worker_deleted)
                    self.monitor_thread.destroyed.connect(self.on_monitor_thread_deleted)

                    self.monitor_thread.start()

                else:
                    print(f"Launch failed (startDetached returned {success}, PID: {pid}).")
                    self.status_label.setText("Launch failed. Is 'umu-run' installed?")
                    self.status_label.setStyleSheet("color: red;")
                    self.current_wine_prefix = None  # Clear prefix if launch failed
                    # Unzip thread/worker will be cleaned up by their signals

            except Exception as e:
                self.status_label.setText(f"Launch failed: {e}")
                self.status_label.setStyleSheet("color: red;")
                self.current_wine_prefix = None  # Clear prefix on exception
                # Unzip thread/worker will be cleaned up by their signals

        # --- Final Cleanup (Partial) ---
        # We NO LONGER clear self.current_install_config here.
        # It's needed in on_run_finished.

        # We clean up self.run_process now as it's not needed
        if self.run_process:
            self.run_process.deleteLater()
            self.run_process = None

    @pyqtSlot(str)
    def on_unzip_error(self, message: str):
        """Called when the worker reports an error."""
        if self.progress_dialog:
            self.progress_dialog.close()
        self.install_button.setEnabled(True)
        self.status_label.setText(f"Install failed: {message}")
        self.status_label.setStyleSheet("color: red;")

        self.current_install_config = None  # Clean up
        # self.thread and self.worker are cleaned up by destroyed signals

    @pyqtSlot()
    def cancel_unzip(self):
        """Called when the 'Cancel' button on the dialog is clicked."""
        if self.worker:
            self.worker.stop()  # Tell worker to stop
        if self.thread:
            self.thread.quit()  # Ask thread to quit

        # Also stop the monitor if it's running
        if self.monitor_worker:
            self.monitor_worker.stop()
        if self.monitor_thread:
            self.monitor_thread.quit()  # Ask to quit

        self.status_label.setText("Install cancelled")
        self.install_button.setEnabled(True)

        self.current_install_config = None  # Clean up

        if self.run_process:
            self.run_process.deleteLater()
            self.run_process = None

        # All threads/workers will be nulled by their
        # 'destroyed' signal handlers.

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

        # --- Check for .desktop files ---
        if self.current_wine_prefix and os.path.isdir(self.current_wine_prefix):
            shortcuts_dir = os.path.join(self.current_wine_prefix, "drive_c", "proton_shortcuts")
            print(f"Checking for shortcuts in: {shortcuts_dir}")

            if os.path.isdir(shortcuts_dir):
                desktop_files = glob.glob(os.path.join(shortcuts_dir, "*.desktop"))

                # --- NEW: Create working shortcuts on desktop ---
                if desktop_files:
                    print(f"Found {len(desktop_files)} .desktop files. Processing...")
                    self.create_desktop_shortcuts(desktop_files)
                else:
                    print("No .desktop files found in proton_shortcuts.")
            else:
                print("proton_shortcuts directory does not exist.")
        else:
            print("WINEPREFIX path not set or does not exist, skipping shortcut check.")

        # --- Final Status Update ---
        # We assume normal exit since we can't get an exit code
        size = self.record.get("total_bytes", 0)
        self.status_label.setText(f"Completed ({self.format_size(size)})")
        self.status_label.setStyleSheet("")

        # --- Final Cleanup ---
        self.current_install_config = None  # Now we can clear this
        self.current_wine_prefix = None

        # self.thread and self.worker (unzip) are already cleaned up
        # self.monitor_thread and self.monitor_worker are
        # currently being cleaned up and will be nulled by their
        # 'destroyed' signal handlers.

    def create_desktop_shortcuts(self, desktop_files: list):
        """
        Parses the found .desktop files, fixes them, and saves them
        to the user's ~/Desktop directory.
        """
        if not self.current_install_config:
            # This should not happen, but as a safeguard
            print("Error: Install config was cleared too early. Cannot create shortcuts.")
            self.current_install_config = {}  # Use empty config
        dirs = list()
        desktop_dir = os.path.join(os.path.expanduser("~"), get_xdg_user_dir("DESKTOP"))
        applications_dir = os.path.join(os.path.expanduser("~"), ".local/share/applications")
        dirs.append(applications_dir)
        dirs.append(desktop_dir)

        for dir in dirs:
            os.makedirs(dir, exist_ok=True)

            for original_path in desktop_files:
                try:
                    print(f"Processing: {os.path.basename(original_path)}")

                    # 1. Read and Parse the original .desktop file

                    # configparser needs a section header
                    with open(original_path, 'r') as f:
                        content = f.read()
                    if not content.strip().startswith('[Desktop Entry]'):
                        content = '[Desktop Entry]\n' + content

                    # Use strict=False to allow duplicate keys
                    config_parser = configparser.ConfigParser(strict=False)

                    # --- THIS IS THE FIX ---
                    # Tell configparser to preserve key case (e.g., 'Type' not 'type')
                    config_parser.optionxform = str
                    # --- END FIX ---

                    config_parser.read_string(content)

                    if 'Desktop Entry' not in config_parser:
                        print(f"Skipping {os.path.basename(original_path)}: Cannot find [Desktop Entry] section.")
                        continue

                    entry = config_parser['Desktop Entry']

                    # --- NEW: Fix the Icon Path ---
                    icon_name = entry.get('Icon')
                    if icon_name:
                        # The icon_name is just a basename, e.g., "arx_fatalis"
                        # We find the icon folder relative to the .desktop file
                        shortcuts_dir = os.path.dirname(original_path)
                        icons_base_dir = os.path.join(shortcuts_dir, "icons")

                        # As per your info: .../proton_shortcuts/icons/[SIZE]/apps/[icon_name].png

                        # Let's find the best one, starting with highest res
                        sizes_to_check = ["256x256", "128x128", "64x64", "48x48", "32x32"]
                        found_icon_path = None

                        for size in sizes_to_check:
                            # Check for common names like "icon_name.png"
                            path_with_png = os.path.join(icons_base_dir, size, "apps", f"{icon_name}.png")
                            # Check for names that might already have an extension, like "icon_name.ico"
                            path_as_is = os.path.join(icons_base_dir, size, "apps", icon_name)

                            if os.path.exists(path_with_png):
                                found_icon_path = path_with_png
                                break  # Stop at the first (highest-res) one we find
                            elif os.path.exists(path_as_is):
                                found_icon_path = path_as_is
                                break  # Stop at the first (highest-res) one we find

                        if found_icon_path:
                            # We found it! Update the parser to use the FULL, ABSOLUTE path.
                            # This makes the .desktop file self-contained.
                            config_parser.set('Desktop Entry', 'Icon', found_icon_path)
                            print(f"Updated icon path to: {found_icon_path}")
                        else:
                            print(f"Warning: Could not find icon '{icon_name}' in {icons_base_dir}")
                    # --- END NEW CODE ---

                    # 2. Extract the CRITICAL information
                    working_dir = entry.get('Path')
                    # Use StartupWMClass as the .exe name. Fallback to guessing from Name.
                    exe_name = entry.get('StartupWMClass')
                    if not exe_name:
                        exe_name = entry.get('Name', 'game') + ".exe"
                        print(f"Warning: No StartupWMClass. Guessing exe name: {exe_name}")

                    if not working_dir:
                        print(f"Skipping {os.path.basename(original_path)}: No 'Path' entry found.")
                        continue

                    # 3. Construct the new, working Exec line
                    exe_path = os.path.join(working_dir, exe_name)
                    config = self.current_install_config or {}

                    proton_path = os.getenv("PROTONPATH", "GE-Proton")
                    env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{self.current_wine_prefix}\" "

                    for key, value in config.items():
                        env_prefix += f"{key}=\"{value}\" "

                    # The final command: cd to working dir, set env, run umu-run
                    # Note: We use single quotes for the 'sh -c' command and
                    # double quotes inside for the paths.
                    new_exec = f"/bin/sh -c 'cd \"{working_dir}\" && {env_prefix} umu-run \"{exe_path}\"'"

                    # 4. Update the parser with the new Exec line
                    config_parser.set('Desktop Entry', 'Exec', new_exec)
                    # Ensure Type is set
                    config_parser.set('Desktop Entry', 'Type', 'Application')
                    config_parser.set('Desktop Entry', 'Categories', 'Application;Game;')

                    # 5. Write the new, fixed file to the user's Desktop
                    new_file_name = os.path.basename(original_path)
                    new_file_path = os.path.join(dir, new_file_name)

                    with open(new_file_path, 'w') as f:
                        config_parser.write(f)

                    # 6. Make the new file executable
                    os.chmod(new_file_path, 0o755)  # rwxr-xr-x

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
            self.button_stack.setCurrentIndex(0)  # Show active (Cancel)
            self.update_progress()

        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.record["status"] = "Completed"
            self.record["total_bytes"] = total_bytes
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

    def __init__(self, data_path: str, umu_database: UmuDatabase, parent=None):
        super().__init__(parent)
        self.umu_database = umu_database
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
        controller = DownloadItemWidget(self.umu_database,
                                        download_item=download_item,
                                        initial_total_size=total_size
                                        )

        controller.finished.connect(self.on_download_finished)
        controller.remove_requested.connect(self.remove_download_item)

        existing_controller = self.find_controller_by_url(download_item.url().toString())
        if existing_controller:
            self.remove_download_item(existing_controller)

        self.insert_row_at(0, controller)
        if controller.record not in self.download_records:
            self.download_records.insert(0, controller.record)

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
            self.download_records = [c.record for c in self.widget_map.keys()]
            # Ensure they are saved in the order they appear (newest first)

            # A simple map-based rebuild might mess up the order.
            # Let's get them from the layout instead.
            records = []
            for row in range(self.downloads_layout.rowCount() - 1):  # -1 to skip stretch
                item = self.downloads_layout.itemAtPosition(row, 1)  # Get filename label
                if item:
                    widget = item.widget()
                    if not widget: continue  # Widget might be in deletion process
                    filename = widget.text()
                    # Find the controller associated with this filename
                    for controller in self.widget_map.keys():
                        if os.path.basename(controller.record["path"]) == filename:
                            records.append(controller.record)
                            break

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
        for row in range(current_row_count - 2, row_index - 1, -1):  # (end, start, step)
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