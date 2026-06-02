import glob
import logging
import os
import shutil
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

from gameyfin_frontend.dialogs import LaunchLoadingDialog, SelectShortcutsDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.utils import (
    create_shortcuts, resolve_shortcut_game_info,
    format_size, parse_size,
)
from gameyfin_frontend.config import COLOR_STATUS_DOWNLOADING, COLOR_STATUS_INSTALLING
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.services import LauncherResolver, GameInstaller, GameLauncher
from gameyfin_frontend.settings import SettingsManager

logger = logging.getLogger(__name__)


class DownloadItemWidget(QWidget):
    remove_requested = pyqtSignal(QWidget)
    finished = pyqtSignal(dict)
    installation_finished = pyqtSignal()

    def __init__(self, umu_database: UmuDatabase, worker: StreamDownloadWorker | None = None, record: dict[str, Any] | None = None,
                 parent: QWidget | None = None, settings: SettingsManager | None = None):
        """Create a download item widget showing progress, status, and action buttons.

        Args:
            umu_database: UmuDatabase instance for UMU game lookups.
            worker: Optional StreamDownloadWorker for active downloads.
            record: Optional persisted download record dict for restoring a previous state.
            parent: Parent widget.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__(parent)
        self.umu_database = umu_database
        self.settings = settings
        self.record = record or {}
        self.last_time = time.time()
        self.last_bytes = 0
        self.last_speed_str = ""

        self.thread = None
        self.worker = None
        self.current_install_config = None

        self.run_process = None
        self.current_wine_prefix = None
        self._loading_dialog = None

        self.monitor_thread = None
        self.monitor_worker = None

        self._launcher_resolver = LauncherResolver()
        self._game_installer = GameInstaller(umu_database, settings, self)
        self._game_launcher = GameLauncher()

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
        self.status_label.setText(f"Running... ({format_size(size)})")
        self.status_label.setStyleSheet(f"color: {COLOR_STATUS_DOWNLOADING};")

    def _handle_launcher_selection(self, target_dir: str) -> str | None:
        """Delegate to LauncherResolver and update UI on special outcomes."""
        def on_no_exe() -> None:
            self.status_label.setText("Install complete, no .exe found.")
            self.status_label.setStyleSheet(f"color: {COLOR_STATUS_INSTALLING};")
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))

        def on_no_launcher() -> None:
            self.status_label.setText("Install complete, no launcher selected.")
            self.status_label.setStyleSheet(f"color: {COLOR_STATUS_INSTALLING};")

        def on_cancelled() -> None:
            self.status_label.setText("Install complete, launch cancelled.")
            self.status_label.setStyleSheet("")

        return self._launcher_resolver.handle_launcher_selection(
            target_dir=target_dir,
            parent=self,
            on_no_exe=on_no_exe,
            on_no_launcher=on_no_launcher,
            on_cancelled=on_cancelled,
        )

    def update_ui_for_historic_state(self) -> None:
        """Update the UI to reflect a previously saved download state (completed, failed, or cancelled).

        Reads from ``self.record`` to restore progress, status text, and button visibility.
        """
        status = self.record.get("status", "Failed")
        self.progress_bar.show()

        if status == "Completed":
            self.progress_bar.setValue(100)
            size = self.record.get("total_bytes", 0)
            self.status_label.setText(f"Completed ({format_size(size)})")

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

    def _on_remove_clicked(self) -> None:
        """Show a confirmation dialog to remove from list only or also delete the folder.

        Emits ``remove_requested`` with the controller depending on user choice.
        """
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
            try:
                shutil.rmtree(target_dir)
            except OSError as e:
                QMessageBox.critical(self, "Error", f"Failed to delete folder:\n{e}")
                return
            self.remove_requested.emit(self)

    def cancel_download(self) -> None:
        """Cancel the current download via the worker."""
        if self.worker:
            self.worker.stop()

    def open_folder(self) -> None:
        """Open the download's target directory in the file manager."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.record.get("path", "")))

    def on_install_clicked(self) -> None:
        """Start the installation process for the downloaded game files."""
        self.proceed_to_installation(self.record["path"])

    @pyqtSlot()
    def _on_worker_deleted(self) -> None:
        """Clear the worker reference when the worker object is deleted."""
        self.worker = None

    @pyqtSlot()
    def _on_thread_deleted(self) -> None:
        """Clear the thread reference when the thread object is deleted."""
        self.thread = None

    @pyqtSlot("long long", "long long")
    def _on_bytes_received(self, received: int, total: int) -> None:
        """Update the status label with received bytes and computed download speed."""
        if total > 0:
            self.record["total_bytes"] = total
        else:
            total = self.record.get("total_bytes", 0)

        if total > 0 and received > 0:
            pct = min(int(received / total * 100), 99)
            self.progress_bar.setValue(pct)

        now = time.time()
        elapsed = now - self.last_time
        if elapsed > 0.5:
            speed = max(0.0, (received - self.last_bytes) / elapsed)
            self.last_speed_str = f"({format_size(int(speed))}/s)"
            self.last_time, self.last_bytes = now, received

        if total > 0:
            self.status_label.setText(f"{format_size(received)} / {format_size(total)} {self.last_speed_str}")
        elif received > 0:
            self.status_label.setText(f"{format_size(received)} {self.last_speed_str}")
        else:
            self.status_label.setText(f"Starting... {self.last_speed_str}")

    @pyqtSlot()
    def on_download_finished(self) -> None:
        """Handle download completion: update UI, mark record as completed, emit finished signal."""
        total = self.record.get("total_bytes", 0)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"Completed ({format_size(total)})")
        self.status_label.setStyleSheet("")

        self._show_completed_buttons()

        self.record["status"] = "Completed"
        self.finished.emit(self.record)

    @pyqtSlot(str)
    def on_download_error(self, message: str) -> None:
        """Handle download error: hide progress bar, show failure status, emit finished signal."""
        self.progress_bar.hide()
        self.status_label.setText(f"Failed: {message}")
        self.status_label.setStyleSheet("color: red;")

        self._show_failed_buttons()

        self.record["status"] = "Failed"
        self.finished.emit(self.record)

    def proceed_to_installation(self, target_dir: str) -> None:
        """Orchestrate the installation: detect UMU game ID, show config dialog, then launch.

        On Linux, searches the UMU database for a matching game, prompts the user for
        configuration, then starts the installation via ``_start_linux_installation``.
        On Windows, launches the selected executable directly.
        """
        if sys.platform == "win32":
            launcher_to_run = self._handle_launcher_selection(target_dir)
            if launcher_to_run is None:
                return  # User cancelled or error

            self._start_windows_installation(launcher_to_run)
            return

        # --- Linux Logic ---
        umu_id, store = self._game_installer.detect_umu_game_id(target_dir)
        wine_prefix_path = self._game_installer.build_wine_prefix(target_dir)
        self.current_wine_prefix = wine_prefix_path

        install_config = self._game_installer.prompt_install_config(
            umu_id=umu_id,
            store=store,
            wine_prefix_path=wine_prefix_path,
        )
        if install_config is None:
            self.status_label.setText("Install cancelled by user.")
            self.status_label.setStyleSheet("")
            self.current_wine_prefix = None
            return

        self.current_install_config = install_config

        launcher_to_run = self._handle_launcher_selection(target_dir)
        if launcher_to_run is None:
            return  # User cancelled or no .exe found

        self._start_linux_installation(launcher_to_run, target_dir, self.current_install_config)

    def _start_windows_installation(self, launcher_to_run: str) -> None:
        """Launch the game executable directly via QProcess (Windows path).

        Args:
            launcher_to_run: Absolute path to the .exe to launch.
        """
        self.run_process = self._game_launcher.start_windows(launcher_to_run)
        if self.run_process is None:
            self.status_label.setText("Launch failed.")
            self.status_label.setStyleSheet("color: red;")
            return
        self.run_process.finished.connect(self.on_run_finished)
        self._set_running_status()

    def _start_linux_installation(self, launcher_to_run: str, target_dir: str, install_config: dict[str, Any]) -> None:
        """Launch the game via UMU environment prefix and umu-run on Linux.

        Shows a loading dialog while Proton initializes.

        Args:
            launcher_to_run: Path to the game executable.
            target_dir: Download target directory (unused, for future use).
            install_config: Dict of environment variables and UMU settings.
        """
        # Show loading dialog before launching
        game_name = self.filename_label.text()
        self._loading_dialog = LaunchLoadingDialog(game_name, parent=self)
        self._loading_dialog.show()

        proton_path = self.settings.get("PROTONPATH") if self.settings else None
        self.run_process = self._game_launcher.start_linux(
            launcher_to_run=launcher_to_run,
            target_dir=target_dir,
            install_config=install_config,
            wine_prefix_path=self.current_wine_prefix or "",
            proton_path=proton_path,
        )
        if self.run_process is None:
            self._loading_dialog.close()
            self._loading_dialog = None
            self.status_label.setText("Launch failed. Is 'umu-run' installed?")
            self.status_label.setStyleSheet("color: red;")
            self.current_wine_prefix = None
            return
        self.run_process.finished.connect(self.on_run_finished)
        self.run_process.finished.connect(self._loading_dialog.close)  # Close loading dialog when game process ends
        self._set_running_status()

    @pyqtSlot()
    @pyqtSlot(int, QProcess.ExitStatus)
    def on_run_finished(self, exit_code: int | None = None, exit_status: QProcess.ExitStatus | None = None) -> None:
        """Handle UMU process completion: emit installation_finished, prompt for shortcuts, update UI.

        Args:
            exit_code: The numeric exit code of the process.
            exit_status: The process exit status enum.
        """
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
        self.status_label.setText(f"Completed ({format_size(size)})")
        self.status_label.setStyleSheet("")

        self.current_install_config = None
        self.current_wine_prefix = None

    def create_desktop_shortcuts(self, all_desktop_files: list[str], selected_desktop: list[str], selected_apps: list[str]) -> None:
        """Create helper .sh scripts and system .desktop shortcuts for the installed game.

        Uses ``resolve_shortcut_game_info`` and ``create_shortcuts`` from utils.

        Args:
            all_desktop_files: All detected .desktop files in the game.
            selected_desktop: Basenames to place on the user's Desktop.
            selected_apps: Basenames to place in ~/.local/share/applications.
        """
        if not self.current_install_config:
            logger.error("Install config was cleared too early. Cannot create shortcuts.")
            self.current_install_config = {}

        game_name, proton_path = resolve_shortcut_game_info(
            self.current_wine_prefix, self.current_install_config
        )
        shortcut_scripts_path = self.settings.get_shortcuts_dir(game_name) if self.settings else ""

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
