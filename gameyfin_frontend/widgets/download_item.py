import glob
import json
import logging
import os
import sys
import time
from typing import Any

from PyQt6.QtCore import pyqtSlot, QProcess, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QProgressBar,
    QPushButton,
    QHBoxLayout,
    QLabel,
    QStyle,
    QMessageBox,
)

from gameyfin_frontend.dialogs import SelectShortcutsDialog, InstallConfigDialog, SelectUmuIdDialog, \
    SelectLauncherDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.utils import (
    create_shortcuts, build_umu_env_prefix
)
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.settings import settings_manager

logger = logging.getLogger(__name__)


class DownloadItemWidget(QWidget):
    remove_requested = pyqtSignal(QWidget)
    finished = pyqtSignal(dict)
    installation_finished = pyqtSignal()

    def __init__(self, umu_database: UmuDatabase, worker: StreamDownloadWorker | None = None, record: dict[str, Any] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.umu_database = umu_database
        self.record = record or {}
        self.last_time = time.time()
        self.last_bytes = 0
        self.last_speed_str = ""

        self.thread = None
        self.worker = None
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
        self.open_folder_button = QPushButton("Open Folder")
        self.remove_button = QPushButton("Remove")
        self.install_button = QPushButton("Install")

        self.button_container = QWidget()
        self.button_layout = QHBoxLayout(self.button_container)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.addWidget(self.cancel_button)
        self.button_layout.addWidget(self.install_button)
        self.button_layout.addWidget(self.open_folder_button)
        self.button_layout.addWidget(self.remove_button)

        font_metrics = self.fontMetrics()
        self.icon_label.setFixedWidth(font_metrics.height())
        self.status_label.setMinimumWidth(font_metrics.horizontalAdvance("Completed (999.99 MB)") + 10)
        self.progress_bar.setMinimumWidth(100)
        self.progress_bar.setMaximumHeight(font_metrics.height() + 4)

        self.cancel_button.clicked.connect(self.cancel_download)
        self.open_folder_button.clicked.connect(self.open_folder)
        self.install_button.clicked.connect(self.on_install_clicked)
        self.remove_button.clicked.connect(self._on_remove_clicked)

        display_name = self.record.get("filename") or os.path.basename(self.record.get("path", ""))
        self.filename_label.setText(os.path.splitext(display_name)[0])

        if worker:
            self._start_worker(worker)
        elif self.record:
            self.update_ui_for_historic_state()

    def _start_worker(self, worker: StreamDownloadWorker):
        """Start the download worker thread and connect signals."""
        self.worker = worker
        self.thread = QThread()
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.bytes_received.connect(self._on_bytes_received)
        self.worker.finished.connect(self.on_download_finished)
        self.worker.error.connect(self.on_download_error)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.worker.destroyed.connect(self._on_worker_deleted)
        self.thread.destroyed.connect(self._on_thread_deleted)

        self.cancel_button.show()
        self.install_button.hide()
        self.open_folder_button.hide()
        self.remove_button.hide()
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting download...")

        self.thread.start()

    def get_widgets_for_grid(self) -> list[QWidget]:
        """Return the list of widgets to add to the download manager grid."""
        return [self.icon_label, self.filename_label, self.progress_bar, self.status_label, self.button_container]

    def _show_completed_buttons(self):
        """Show the buttons visible after download completion (Install, Open Folder, Remove)."""
        self.cancel_button.hide()
        self.install_button.show()
        self.open_folder_button.show()
        self.remove_button.show()

    def _show_failed_buttons(self):
        """Show only the Remove button after download failure or cancellation."""
        self.cancel_button.hide()
        self.install_button.hide()
        self.open_folder_button.hide()
        self.remove_button.show()

    def _set_running_status(self):
        """Set the status label to 'Running...' with size info."""
        size = self.record.get("total_bytes", 0)
        self.status_label.setText(f"Running... ({self.format_size(size)})")
        self.status_label.setStyleSheet("color: #3498DB;")

    def _find_launcher_paths(self, target_dir: str) -> list[str]:
        """Walk target_dir and collect all .exe files."""
        launcher_paths = []
        try:
            for root, dirs, files in os.walk(target_dir):
                for file in files:
                    if file.lower().endswith(".exe"):
                        launcher_paths.append(os.path.join(root, file))
        except OSError as e:
            logger.error("Error searching for launcher: %s", e)
        return launcher_paths

    def _handle_launcher_selection(self, target_dir: str) -> str | None:
        """Search for .exe files and let user select one if multiple found.

        Returns the selected launcher path, or None if user cancelled or
        an error occurred.
        """
        launcher_paths = self._find_launcher_paths(target_dir)

        if not launcher_paths:
            self.status_label.setText("Install complete, no .exe found.")
            self.status_label.setStyleSheet("color: #E67E22;")
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))
            return None

        if len(launcher_paths) == 1:
            return launcher_paths[0]

        dialog = SelectLauncherDialog(target_dir, launcher_paths, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            launcher_to_run = dialog.get_selected_launcher()
            if not launcher_to_run:
                self.status_label.setText("Install complete, no launcher selected.")
                self.status_label.setStyleSheet("color: #E67E22;")
                return None
            return launcher_to_run
        else:
            self.status_label.setText("Install complete, launch cancelled.")
            self.status_label.setStyleSheet("")
            return None

    def update_ui_for_historic_state(self):
        """Update UI to reflect a previously saved download state (completed, failed, or cancelled)."""
        status = self.record.get("status", "Failed")
        self.progress_bar.show()

        if status == "Completed":
            self.progress_bar.setValue(100)
            size = self.record.get("total_bytes", 0)
            self.status_label.setText(f"Completed ({self.format_size(size)})")

            self._show_completed_buttons()

            target_dir = self.record.get("path", "")
            if not os.path.isdir(target_dir):
                self.status_label.setText("Directory not found")
                self.status_label.setStyleSheet("color: red; font-weight: bold;")
                self.open_folder_button.setEnabled(False)
                self.install_button.setEnabled(False)
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
                self.icon_label.setPixmap(icon.pixmap(self.icon_label.sizeHint()))

        elif status in ("Cancelled", "Failed"):
            self.progress_bar.hide()
            self.status_label.setText(status)

            self._show_failed_buttons()

    def _on_remove_clicked(self):
        """Show a confirmation dialog to remove from list only or also delete the folder."""
        target_dir = self.record.get("path", "")
        dir_exists = os.path.isdir(target_dir)

        msg = QMessageBox(self)
        msg.setWindowTitle("Remove Download")
        msg.setText("How do you want to remove this entry?")

        remove_entry_btn = msg.addButton("Remove from list", QMessageBox.ButtonRole.ActionRole)
        if dir_exists:
            remove_all_btn = msg.addButton("Remove from list + delete folder", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)

        msg.exec()
        clicked = msg.clickedButton()

        if clicked == remove_entry_btn:
            self.remove_requested.emit(self)
        elif dir_exists and clicked == remove_all_btn:
            import shutil
            try:
                shutil.rmtree(target_dir)
            except OSError as e:
                QMessageBox.critical(self, "Error", f"Failed to delete folder:\n{e}")
                return
            self.remove_requested.emit(self)

    def cancel_download(self):
        """Cancel the current download via the worker."""
        if self.worker:
            self.worker.stop()

    def open_folder(self):
        """Open the download's target directory in the file manager."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.record.get("path", "")))

    def on_install_clicked(self):
        """Start the installation process for the downloaded game files."""
        self.proceed_to_installation(self.record["path"])

    @pyqtSlot()
    def _on_worker_deleted(self):
        self.worker = None

    @pyqtSlot()
    def _on_thread_deleted(self):
        self.thread = None

    @pyqtSlot('long long', 'long long')
    def _on_bytes_received(self, received: int, total: int):
        if total > 0:
            self.record["total_bytes"] = total
        else:
            total = self.record.get("total_bytes", 0)

        now = time.time()
        elapsed = now - self.last_time
        if elapsed > 0.5:
            speed = max(0.0, (received - self.last_bytes) / elapsed)
            self.last_speed_str = f"({self.format_size(int(speed))}/s)"
            self.last_time, self.last_bytes = now, received

        if total > 0:
            self.status_label.setText(f"{self.format_size(received)} / {self.format_size(total)} {self.last_speed_str}")
        else:
            self.status_label.setText(f"{self.format_size(received)} / ??? {self.last_speed_str}")

    @pyqtSlot()
    def on_download_finished(self):
        total = self.record.get("total_bytes", 0)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"Completed ({self.format_size(total)})")
        self.status_label.setStyleSheet("")

        self._show_completed_buttons()

        self.record["status"] = "Completed"
        self.finished.emit(self.record)

    @pyqtSlot(str)
    def on_download_error(self, message: str):
        self.progress_bar.hide()
        self.status_label.setText(f"Failed: {message}")
        self.status_label.setStyleSheet("color: red;")

        self._show_failed_buttons()

        self.record["status"] = "Failed"
        self.finished.emit(self.record)

    def proceed_to_installation(self, target_dir: str) -> None:
        """Orchestrate the installation: detect UMU game ID, show config dialog, then launch."""
        if sys.platform == "win32":
            launcher_to_run = self._handle_launcher_selection(target_dir)
            if launcher_to_run is None:
                return  # User cancelled or error

            self._start_windows_installation(launcher_to_run)
            return

        # --- Linux Logic ---
        default_game_id = "umu-default"
        default_store = "none"
        results = []

        try:
            json_files = glob.glob(os.path.join(target_dir, "product_*.json"))
            if json_files:
                product_json_path = json_files[0]
                logger.info("Found product info: %s", product_json_path)

                with open(product_json_path, 'r') as f:
                    product_data = json.load(f)

                codename = product_data.get("id")

                if codename:
                    logger.info("Found codename: %s", codename)
                    results = self.umu_database.get_game_by_codename(str(codename))
                    logger.info("API results (by codename): %s", results)

            if not results:
                filename = self.record.get("filename", os.path.basename(self.record.get("path", "")))
                zip_name_base = os.path.splitext(filename)[0]
                search_title = zip_name_base.replace('_', ' ').replace('-', ' ').strip()

                if search_title:
                    logger.info("No results from codename. Fallback: searching by title: '%s'", search_title)
                    results = self.umu_database.search_by_partial_title(search_title)
                    logger.info("API results (by title): %s", results)
                else:
                    logger.info("No codename found and filename was empty. Skipping UMU search.")

            selected_entry = None
            if isinstance(results, list) and len(results) > 0:
                if len(results) == 1:
                    selected_entry = results[0]
                    logger.info("One matching entry found.")
                else:
                    logger.info("Multiple matching entries found, showing dialog.")
                    umu_dialog = SelectUmuIdDialog(results, self)
                    if umu_dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_entry = umu_dialog.get_selected_entry()
                    else:
                        logger.info("User cancelled UMU ID selection.")

                if selected_entry:
                    default_game_id = selected_entry.get("umu_id", default_game_id)
                    default_store = selected_entry.get("store", default_store)
                    logger.info("Using: umu_id=%s, store=%s", default_game_id, default_store)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Error during UMU auto-detection: %s", e)

        wine_prefix_path = None
        if sys.platform == "linux":
            try:
                folder_name = os.path.basename(target_dir)
                pfx_name = f"{folder_name.lower()}_pfx"
                wine_prefix_path = os.path.join(settings_manager.get_prefixes_dir(), pfx_name)

                self.current_wine_prefix = wine_prefix_path
                logger.info("Getting WINEPREFIX for dialog: %s", wine_prefix_path)
            except OSError as e:
                logger.error("Error getting WINEPREFIX: %s", e)

        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self,
            default_game_id=default_game_id,
            default_store=default_store,
            wine_prefix_path=wine_prefix_path
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_label.setText("Install cancelled by user.")
            self.status_label.setStyleSheet("")
            self.current_wine_prefix = None
            return

        self.current_install_config = dialog.get_config()

        launcher_to_run = self._handle_launcher_selection(target_dir)
        if launcher_to_run is None:
            return  # User cancelled or no .exe found

        self._start_linux_installation(launcher_to_run, target_dir, self.current_install_config)

    def _start_windows_installation(self, launcher_to_run: str) -> None:
        """Launch the game executable directly via QProcess (Windows path)."""
        try:
            logger.info("Executing (Windows): %s", launcher_to_run)
            self.run_process = QProcess(self)
            self.run_process.setProgram(launcher_to_run)
            self.run_process.setWorkingDirectory(os.path.dirname(launcher_to_run))

            self.run_process.finished.connect(self.on_run_finished)
            self.run_process.start()

            if not self.run_process.waitForStarted():
                logger.info("Launch failed (QProcess failed to start).")
                self.status_label.setText("Launch failed.")
                self.status_label.setStyleSheet("color: red;")
                return

            self._set_running_status()
        except OSError as e:
            self.status_label.setText(f"Launch failed: {e}")
            self.status_label.setStyleSheet("color: red;")

    def _start_linux_installation(self, launcher_to_run: str, target_dir: str, install_config: dict[str, Any]) -> None:
        """Launch the game via UMU environment prefix and umu-run on Linux."""
        try:
            config = install_config or {}

            if not self.current_wine_prefix:
                raise ValueError("Wineprefix path was not set.")

            wine_prefix_path = self.current_wine_prefix
            launcher_dir = os.path.dirname(launcher_to_run)
            proton_path = settings_manager.get("PROTONPATH", "GE-Proton")

            logger.info("[Install] Applying user environment configuration:")
            for key, value in config.items():
                logger.info("  %s=%s", key, value)

            env_prefix = build_umu_env_prefix(proton_path, wine_prefix_path, config)
            self.run_process = QProcess(self)
            self.run_process.setWorkingDirectory(launcher_dir)

            command_string = f"{env_prefix} exec umu-run \"{launcher_to_run}\""
            logger.info("Executing: /bin/sh -c \"%s\"", command_string)
            self.run_process.finished.connect(self.on_run_finished)
            self.run_process.start("/bin/sh", ["-c", command_string])

            if not self.run_process.waitForStarted():
                logger.info("Launch failed (QProcess failed to start).")
                self.status_label.setText("Launch failed. Is 'umu-run' installed?")
                self.status_label.setStyleSheet("color: red;")
                self.current_wine_prefix = None
                return

            self._set_running_status()
        except (ValueError, OSError) as e:
            self.status_label.setText(f"Launch failed: {e}")
            self.status_label.setStyleSheet("color: red;")
            self.current_wine_prefix = None

    @pyqtSlot()
    @pyqtSlot(int, QProcess.ExitStatus)
    def on_run_finished(self, exit_code: int | None = None, exit_status: QProcess.ExitStatus | None = None) -> None:
        logger.info("umu-run process finished with code %s, status %s.", exit_code, exit_status)

        self.installation_finished.emit()

        if self.current_wine_prefix and os.path.isdir(self.current_wine_prefix):
            shortcuts_dir = os.path.join(self.current_wine_prefix, "drive_c", "proton_shortcuts")
            logger.info("Checking for shortcuts in: %s", shortcuts_dir)

            if os.path.isdir(shortcuts_dir):
                desktop_files = glob.glob(os.path.join(shortcuts_dir, "*.desktop"))

                if desktop_files:
                    logger.info("Found %d potential .desktop files.", len(desktop_files))

                    dialog = SelectShortcutsDialog(desktop_files, self.parentWidget())
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_desktop, selected_apps = dialog.get_selected_files()
                        logger.info("User selected shortcuts to create. Processing...")
                        self.create_desktop_shortcuts(desktop_files, selected_desktop, selected_apps)
                    else:
                        logger.info("User cancelled shortcut creation dialog. Still creating helper scripts.")
                        self.create_desktop_shortcuts(desktop_files, [], [])

                else:
                    logger.info("No .desktop files found in proton_shortcuts.")
            else:
                logger.info("proton_shortcuts directory does not exist.")
        else:
            logger.info("WINEPREFIX path not set or does not exist, skipping shortcut check.")

        size = self.record.get("total_bytes", 0)
        self.status_label.setText(f"Completed ({self.format_size(size)})")
        self.status_label.setStyleSheet("")

        self.current_install_config = None
        self.current_wine_prefix = None

    def create_desktop_shortcuts(self, all_desktop_files: list[str], selected_desktop: list[str], selected_apps: list[str]) -> None:
        """Create helper .sh scripts and system .desktop shortcuts for the installed game."""
        if not self.current_install_config:
            logger.error("Install config was cleared too early. Cannot create shortcuts.")
            self.current_install_config = {}

        prefix_basename = os.path.basename(self.current_wine_prefix)
        game_name = prefix_basename.removesuffix("_pfx")
        if not game_name:
            game_name = "unknown-game"

        shortcut_scripts_path = settings_manager.get_shortcuts_dir(game_name)
        proton_path = settings_manager.get("PROTONPATH", "GE-Proton")

        create_shortcuts(
            all_desktop_files=all_desktop_files,
            scripts_dir=shortcut_scripts_path,
            wine_prefix=self.current_wine_prefix,
            install_config=self.current_install_config,
            proton_path=proton_path,
            selected_desktop=selected_desktop,
            selected_apps=selected_apps,
            remove_unselected=False,
        )

    @staticmethod
    def format_size(nbytes: int) -> str:
        if nbytes >= 1024 ** 3: return f"{nbytes / 1024 ** 3:.2f} GB"
        if nbytes >= 1024 ** 2: return f"{nbytes / 1024 ** 2:.2f} MB"
        if nbytes >= 1024: return f"{nbytes / 1024:.2f} KB"
        return f"{nbytes} B"
