import configparser
import glob
import json
import os
import sys
import time
import shlex

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
from gameyfin_frontend.utils import get_xdg_user_dir
from gameyfin_frontend.workers import StreamDownloadWorker
from gameyfin_frontend.settings import settings_manager


class DownloadItemWidget(QWidget):
    remove_requested = pyqtSignal(QWidget)
    finished = pyqtSignal(dict)
    installation_finished = pyqtSignal()

    def __init__(self, umu_database: UmuDatabase, worker: StreamDownloadWorker = None, record: dict = None,
                 parent=None):
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
        return [self.icon_label, self.filename_label, self.progress_bar, self.status_label, self.button_container]

    def update_ui_for_historic_state(self):
        status = self.record.get("status", "Failed")
        self.progress_bar.show()

        if status == "Completed":
            self.progress_bar.setValue(100)
            size = self.record.get("total_bytes", 0)
            self.status_label.setText(f"Completed ({self.format_size(size)})")

            self.cancel_button.hide()
            self.install_button.show()
            self.open_folder_button.show()
            self.remove_button.show()

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

            self.cancel_button.hide()
            self.install_button.hide()
            self.open_folder_button.hide()
            self.remove_button.show()

    def _on_remove_clicked(self):
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
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete folder:\n{e}")
                return
            self.remove_requested.emit(self)

    def cancel_download(self):
        if self.worker:
            self.worker.stop()

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.record.get("path", "")))

    def on_install_clicked(self):
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

        self.cancel_button.hide()
        self.install_button.show()
        self.open_folder_button.show()
        self.remove_button.show()

        self.record["status"] = "Completed"
        self.finished.emit(self.record)

    @pyqtSlot(str)
    def on_download_error(self, message: str):
        self.progress_bar.hide()
        self.status_label.setText(f"Failed: {message}")
        self.status_label.setStyleSheet("color: red;")

        self.cancel_button.hide()
        self.install_button.hide()
        self.open_folder_button.hide()
        self.remove_button.show()

        self.record["status"] = "Failed"
        self.finished.emit(self.record)

    def proceed_to_installation(self, target_dir):
        if sys.platform == "win32":
            launcher_paths = []
            try:
                for root, dirs, files in os.walk(target_dir):
                    for file in files:
                        if file.lower().endswith(".exe"):
                            launcher_paths.append(os.path.join(root, file))
            except Exception as e:
                print(f"Error searching for launcher: {e}")
                self.on_download_error(f"Install OK, but failed to find launcher: {e}")
                return

            if not launcher_paths:
                self.status_label.setText("Install complete, no .exe found.")
                self.status_label.setStyleSheet("color: #E67E22;")
                QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))
                return

            launcher_to_run = None
            if len(launcher_paths) == 1:
                launcher_to_run = launcher_paths[0]
            else:
                dialog = SelectLauncherDialog(target_dir, launcher_paths, self)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    launcher_to_run = dialog.get_selected_launcher()
                    if not launcher_to_run:
                        self.status_label.setText("Install complete, no launcher selected.")
                        self.status_label.setStyleSheet("color: #E67E22;")
                        return
                else:
                    self.status_label.setText("Install complete, launch cancelled.")
                    self.status_label.setStyleSheet("")
                    return

            if launcher_to_run:
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
                print(f"Found product info: {product_json_path}")

                with open(product_json_path, 'r') as f:
                    product_data = json.load(f)

                codename = product_data.get("id")

                if codename:
                    print(f"Found codename: {codename}")
                    results = self.umu_database.get_game_by_codename(str(codename))
                    print(f"API results (by codename): {results}")

            if not results:
                filename = self.record.get("filename", os.path.basename(self.record.get("path", "")))
                zip_name_base = os.path.splitext(filename)[0]
                search_title = zip_name_base.replace('_', ' ').replace('-', ' ').strip()

                if search_title:
                    print(f"No results from codename. Fallback: searching by title: '{search_title}'")
                    results = self.umu_database.search_by_partial_title(search_title)
                    print(f"API results (by title): {results}")
                else:
                    print("No codename found and filename was empty. Skipping UMU search.")

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

        wine_prefix_path = None
        if sys.platform == "linux":
            try:
                folder_name = os.path.basename(target_dir)
                pfx_name = f"{folder_name.lower()}_pfx"
                wine_prefix_path = os.path.join(settings_manager.get_prefixes_dir(), pfx_name)

                self.current_wine_prefix = wine_prefix_path
                print(f"Getting WINEPREFIX for dialog: {wine_prefix_path}")
            except Exception as e:
                print(f"Error getting WINEPREFIX: {e}")

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

        launcher_paths = []
        launcher_to_run = None

        try:
            for root, dirs, files in os.walk(target_dir):
                for file in files:
                    if file.lower().endswith(".exe"):
                        launcher_paths.append(os.path.join(root, file))
        except Exception as e:
            print(f"Error searching for launcher: {e}")
            self.on_download_error(f"Install OK, but failed to find launcher: {e}")
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

    def _start_windows_installation(self, launcher_to_run: str):
        try:
            print(f"Executing (Windows): {launcher_to_run}")
            self.run_process = QProcess(self)
            self.run_process.setProgram(launcher_to_run)
            self.run_process.setWorkingDirectory(os.path.dirname(launcher_to_run))

            self.run_process.finished.connect(self.on_run_finished)
            self.run_process.start()

            if self.run_process.waitForStarted():
                print(f"Launch successful. Monitoring QProcess.")
                size = self.record.get("total_bytes", 0)
                self.status_label.setText(f"Running... ({self.format_size(size)})")
                self.status_label.setStyleSheet("color: #3498DB;")
            else:
                print(f"Launch failed (QProcess failed to start).")
                self.status_label.setText("Launch failed.")
                self.status_label.setStyleSheet("color: red;")
        except Exception as e:
            self.status_label.setText(f"Launch failed: {e}")
            self.status_label.setStyleSheet("color: red;")

    def _start_linux_installation(self, launcher_to_run: str, target_dir: str, install_config: dict):
        try:
            config = install_config or {}

            if not self.current_wine_prefix:
                raise ValueError("Wineprefix path was not set.")

            wine_prefix_path = self.current_wine_prefix

            launcher_dir = os.path.dirname(launcher_to_run)

            proton_path = settings_manager.get("PROTONPATH", "GE-Proton")
            env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{wine_prefix_path}\" "
            umu_command = "umu-run"

            print("[Install] Applying user environment configuration:")
            for key, value in config.items():
                print(f"  {key}={value}")
                env_prefix += f"{key}=\"{value}\" "

            self.run_process = QProcess(self)
            self.run_process.setWorkingDirectory(launcher_dir)

            command_string = f"{env_prefix} exec {umu_command} \"{launcher_to_run}\""
            print(f"Executing: /bin/sh -c \"{command_string}\" ")
            self.run_process.finished.connect(self.on_run_finished)
            self.run_process.start("/bin/sh", ["-c", command_string])

            if self.run_process.waitForStarted():
                print(f"Launch successful. Monitoring QProcess.")
                size = self.record.get("total_bytes", 0)
                self.status_label.setText(f"Running... ({self.format_size(size)})")
                self.status_label.setStyleSheet("color: #3498DB;")
            else:
                print(f"Launch failed (QProcess failed to start).")
                self.status_label.setText("Launch failed. Is 'umu-run' installed?")
                self.status_label.setStyleSheet("color: red;")
                self.current_wine_prefix = None

        except Exception as e:
            self.status_label.setText(f"Launch failed: {e}")
            self.status_label.setStyleSheet("color: red;")
            self.current_wine_prefix = None

    @pyqtSlot()
    @pyqtSlot(int, QProcess.ExitStatus)
    def on_run_finished(self, exit_code=None, exit_status=None):
        print(f"umu-run process finished with code {exit_code}, status {exit_status}.")

        self.installation_finished.emit()

        if self.current_wine_prefix and os.path.isdir(self.current_wine_prefix):
            shortcuts_dir = os.path.join(self.current_wine_prefix, "drive_c", "proton_shortcuts")
            print(f"Checking for shortcuts in: {shortcuts_dir}")

            if os.path.isdir(shortcuts_dir):
                desktop_files = glob.glob(os.path.join(shortcuts_dir, "*.desktop"))

                if desktop_files:
                    print(f"Found {len(desktop_files)} potential .desktop files.")

                    dialog = SelectShortcutsDialog(desktop_files, self.parentWidget())
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_desktop, selected_apps = dialog.get_selected_files()
                        print(f"User selected shortcuts to create. Processing...")
                        self.create_desktop_shortcuts(desktop_files, selected_desktop, selected_apps)
                    else:
                        print("User cancelled shortcut creation dialog. Still creating helper scripts.")
                        self.create_desktop_shortcuts(desktop_files, [], [])

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

    def create_desktop_shortcuts(self, all_desktop_files: list, selected_desktop: list, selected_apps: list):
        if not self.current_install_config:
            print("Error: Install config was cleared too early. Cannot create shortcuts.")
            self.current_install_config = {}

        home_dir = os.path.expanduser("~")
        prefix_basename = os.path.basename(self.current_wine_prefix)
        game_name = prefix_basename.removesuffix("_pfx")
        if not game_name:
            game_name = "unknown-game"

        shortcut_scripts_path = settings_manager.get_shortcuts_dir(game_name)
        os.makedirs(shortcut_scripts_path, exist_ok=True)

        for original_path in all_desktop_files:
            try:
                with open(original_path, 'r') as f:
                    content = f.read()
                if not content.strip().startswith('[Desktop Entry]'):
                    content = '[Desktop Entry]\n' + content

                config_parser = configparser.ConfigParser(strict=False)
                config_parser.optionxform = str
                config_parser.read_string(content)

                if 'Desktop Entry' not in config_parser:
                    continue

                entry = config_parser['Desktop Entry']
                working_dir = entry.get('Path')
                exe_name = entry.get('StartupWMClass')
                if not exe_name:
                    exe_name = entry.get('Name', 'game') + ".exe"

                if not working_dir:
                    continue

                exe_path = os.path.join(working_dir, exe_name)
                config = self.current_install_config or {}

                proton_path = settings_manager.get("PROTONPATH", "GE-Proton")
                env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{self.current_wine_prefix}\" "
                umu_command = "umu-run"

                for key, value in config.items():
                    env_prefix += f"{key}=\"{value}\" "

                command_to_run = f"{env_prefix}{umu_command} \"{exe_path}\""
                script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
                script_path = os.path.join(shortcut_scripts_path, script_name)
                script_content = f"#!/bin/sh\n\n# Auto-generated by Gameyfin\n{command_to_run}\n"

                with open(script_path, 'w') as f:
                    f.write(script_content)
                os.chmod(script_path, 0o755)

            except Exception as e:
                print(f"Failed to create helper script for {original_path}: {e}")

        locs = [
            (os.path.join(home_dir, get_xdg_user_dir("DESKTOP")), selected_desktop),
            (os.path.join(home_dir, ".local", "share", "applications"), selected_apps)
        ]

        for target_dir, selected_list in locs:
            os.makedirs(target_dir, exist_ok=True)

            for original_path in selected_list:
                try:
                    print(f"Processing system shortcut: {os.path.basename(original_path)} for {target_dir}")

                    with open(original_path, 'r') as f:
                        content = f.read()
                    if not content.strip().startswith('[Desktop Entry]'):
                        content = '[Desktop Entry]\n' + content

                    config_parser = configparser.ConfigParser(strict=False)
                    config_parser.optionxform = str
                    config_parser.read_string(content)

                    if 'Desktop Entry' not in config_parser:
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
                        else:
                            print(f"Warning: Could not find icon '{icon_name}' in {icons_base_dir}")

                    script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
                    script_path = os.path.join(shortcut_scripts_path, script_name)

                    is_flatpak_env = os.path.exists("/.flatpak-info")
                    use_host_umu = self.current_install_config.get("USE_HOST_UMU", "0")

                    if is_flatpak_env and use_host_umu == "0":
                        inner_cmd = shlex.quote(script_path)
                        for char in ('\\', '"', '$', '`'):
                            inner_cmd = inner_cmd.replace(char, f'\\{char}')

                        config_parser.set('Desktop Entry', 'Exec', f'flatpak run --command=sh org.gameyfin.Gameyfin-Desktop -c "{inner_cmd}"')
                    else:
                        config_parser.set('Desktop Entry', 'Exec', f'"{script_path}"')

                    config_parser.set('Desktop Entry', 'Type', 'Application')
                    config_parser.set('Desktop Entry', 'Categories', 'Application;Game;')

                    new_file_name = os.path.basename(original_path)
                    new_file_path = os.path.join(target_dir, new_file_name)

                    with open(new_file_path, 'w') as f:
                        config_parser.write(f)

                    os.chmod(new_file_path, 0o755)
                    print(f"Successfully created system shortcut at: {new_file_path}")

                except Exception as e:
                    print(f"Failed to process system shortcut {original_path} for {target_dir}: {e}")

    @staticmethod
    def format_size(nbytes: int) -> str:
        if nbytes >= 1024 ** 3: return f"{nbytes / 1024 ** 3:.2f} GB"
        if nbytes >= 1024 ** 2: return f"{nbytes / 1024 ** 2:.2f} MB"
        if nbytes >= 1024: return f"{nbytes / 1024:.2f} KB"
        return f"{nbytes} B"
