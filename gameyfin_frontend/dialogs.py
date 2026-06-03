import logging
import math
import os
import subprocess
import sys
from os import getenv
from os.path import relpath
from typing import Any

from PyQt6.QtCore import pyqtSlot, QTimer, Qt
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import (
    QVBoxLayout, QFormLayout, QCheckBox, QLineEdit, QPushButton, QStyle,
    QHBoxLayout, QWidget, QComboBox, QPlainTextEdit, QDialogButtonBox,
    QLabel, QInputDialog, QDialog, QMessageBox, QListWidget, QScrollArea
)

from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.utils import parse_desktop_file
from gameyfin_frontend.config import DEFAULT_PROTON, UMU_RUN_CMD

logger = logging.getLogger(__name__)


class InstallConfigDialog(QDialog):
    """
    A dialog to configure environment variables before installation.
    """

    def __init__(self, umu_database: UmuDatabase, parent: QWidget | None = None,
                 default_game_id: str = "umu-default", default_store: str = "none",
                 wine_prefix_path: str | None = None, initial_config: dict[str, Any] | None = None,
                 settings: SettingsManager | None = None):
        """Configure UMU installation environment variables (protonfix, Proton path, store, extra env vars).

        Args:
            umu_database: UmuDatabase instance for searching game fixes.
            parent: Parent widget.
            default_game_id: Default UMU ID for the GAMEID field.
            default_store: Default store selection.
            wine_prefix_path: Optional WINE prefix path for wine tools.
            initial_config: Optional dict to pre-populate fields from a prior install.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__(parent)
        self.umu_database = umu_database
        self.wine_prefix_path = wine_prefix_path
        self.settings = settings
        self.setWindowTitle("Installation Configuration")
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.wayland_checkbox = QCheckBox("Enable Wayland")
        self.mangohud_checkbox = QCheckBox("Enable MangoHud")
        self.wow64_checkbox = QCheckBox("Enable WOW64")

        self.gameid_input = QLineEdit()
        self.gameid_input.setText(default_game_id)

        self.search_button = QPushButton()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.search_button.setIcon(icon)
        self.search_button.setToolTip("Search for game by name")

        button_size = self.gameid_input.sizeHint().height()
        self.search_button.setFixedSize(button_size, button_size)

        self.gameid_layout = QHBoxLayout()
        self.gameid_layout.setContentsMargins(0, 0, 0, 0)
        self.gameid_layout.addWidget(self.gameid_input)
        self.gameid_layout.addWidget(self.search_button)
        self.gameid_widget = QWidget()
        self.gameid_widget.setLayout(self.gameid_layout)

        self.protonpath_input = QLineEdit()
        if self.settings:
            self.protonpath_input.setText(self.settings.get("PROTONPATH", DEFAULT_PROTON))
        else:
            self.protonpath_input.setText(DEFAULT_PROTON)

        self.store_combo = QComboBox()
        if self.settings:
            stores = self.settings.get("GF_UMU_DB_STORES", ["none", "gog", "amazon", "battlenet", "ea", "egs",
                                                           "humble", "itchio", "steam", "ubisoft", "zoomplatform"])
        else:
            stores = ["none", "gog", "amazon", "battlenet", "ea", "egs", "humble", "itchio", "steam", "ubisoft", "zoomplatform"]
        self.store_combo.addItems(stores)
        self.store_combo.setCurrentText(default_store)

        self.extra_vars_input = QPlainTextEdit()
        self.extra_vars_input.setPlaceholderText("KEY1=VALUE1\nKEY2=VALUE2")

        # Apply initial config if provided
        if initial_config:
            if initial_config.get("PROTON_ENABLE_WAYLAND") == "1":
                self.wayland_checkbox.setChecked(True)

            if initial_config.get("MANGOHUD") == "1":
                self.mangohud_checkbox.setChecked(True)

            if initial_config.get("PROTON_USE_WOW64") == "1":
                self.wow64_checkbox.setChecked(True)

            if "GAMEID" in initial_config:
                self.gameid_input.setText(initial_config["GAMEID"])

            if "STORE" in initial_config:
                self.store_combo.setCurrentText(initial_config["STORE"])

            if "PROTONPATH" in initial_config:
                self.protonpath_input.setText(initial_config["PROTONPATH"])

            # Populate extra vars
            extra_lines = []
            for k, v in initial_config.items():
                if k not in ["PROTON_ENABLE_WAYLAND", "MANGOHUD", "GAMEID", "STORE", "PROTON_USE_WOW64", "PROTONPATH"]:
                    extra_lines.append(f"{k}={v}")
            self.extra_vars_input.setPlainText("\n".join(extra_lines))

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        main_layout.addWidget(self.wayland_checkbox)
        main_layout.addWidget(self.mangohud_checkbox)
        main_layout.addWidget(self.wow64_checkbox)

        form_layout.addRow("Umu protonfix:", self.gameid_widget)
        form_layout.addRow("Proton Path:", self.protonpath_input)
        form_layout.addRow("Store:", self.store_combo)
        main_layout.addLayout(form_layout)

        main_layout.addWidget(QLabel("Additional Environment Variables (one per line):"))
        main_layout.addWidget(self.extra_vars_input)

        if self.wine_prefix_path:
            prefix_label = QLabel(f"<b>WINE Prefix:</b><br>{self.wine_prefix_path}")
            prefix_label.setWordWrap(True)
            main_layout.addWidget(prefix_label)

        self.wine_tools_widget = QWidget()
        wine_tools_layout = QHBoxLayout(self.wine_tools_widget)
        wine_tools_layout.setContentsMargins(0, 0, 0, 0)

        self.winecfg_button = QPushButton("Run Winecfg")
        self.winetricks_button = QPushButton("Run Winetricks")
        self.regedit_button = QPushButton("Run Regedit")

        wine_tools_layout.addWidget(self.winecfg_button)
        wine_tools_layout.addWidget(self.winetricks_button)
        wine_tools_layout.addWidget(self.regedit_button)

        main_layout.addWidget(self.wine_tools_widget)

        main_layout.addWidget(button_box)

        self.winecfg_button.clicked.connect(self.run_winecfg)
        self.winetricks_button.clicked.connect(self.run_winetricks)
        self.regedit_button.clicked.connect(self.run_regedit)
        self.search_button.clicked.connect(self.search_for_game_id)

    @pyqtSlot()
    def search_for_game_id(self) -> None:
        """
        Opens a dialog to search for a game by title, checks ALL stores,
        and populates the umu_id and store fields from the results.
        """
        text, ok = QInputDialog.getText(self, "Search UMU", "Enter game title to search:")
        if not ok or not text.strip():
            return

        search_title = text.strip()

        all_results = []
        try:
            logger.info("Searching all stores for title: %s...", search_title)

            results = self.umu_database.search_by_partial_title(search_title)

            processed_list = []
            if isinstance(results, list):
                processed_list = results
            elif isinstance(results, dict) and results.get("umu_id"):
                processed_list = [results]

            for entry in processed_list:
                if entry.get("umu_id"):
                    all_results.append(entry)

            if not all_results:
                QMessageBox.information(self, "No Results",
                                        f"No games found matching '{search_title}' in any store.")
                return

            selected_entry = None
            dialog = SelectUmuIdDialog(all_results, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected_entry = dialog.get_selected_entry()

            if selected_entry:
                umu_id = selected_entry.get("umu_id")
                store = selected_entry.get("store")

                if umu_id:
                    self.gameid_input.setText(umu_id)
                if store:
                    self.store_combo.setCurrentText(store)

        except (ValueError, KeyError, TypeError, RuntimeError) as e:
            logger.error("Search error for title '%s': %s", search_title, e)
            QMessageBox.warning(self, "Search Error", f"An error occurred during search:\n{e}")

    @pyqtSlot()
    def run_winecfg(self):
        """Runs winecfg in the correct prefix using umu-run."""
        if not self.wine_prefix_path:
            return

        os.makedirs(self.wine_prefix_path, exist_ok=True)

        proton_path = self.settings.get("PROTONPATH", DEFAULT_PROTON) if self.settings else DEFAULT_PROTON

        proc_env = os.environ.copy()
        proc_env["PROTONPATH"] = proton_path
        proc_env["WINEPREFIX"] = self.wine_prefix_path

        logger.info("Starting winecfg with PROTONPATH=%s WINEPREFIX=%s", proton_path, self.wine_prefix_path)
        subprocess.Popen([UMU_RUN_CMD, "winecfg"], env=proc_env, start_new_session=True)

    @pyqtSlot()
    def run_winetricks(self):
        """Runs winetricks in the correct prefix using the bundled binary."""
        if not self.wine_prefix_path:
            return

        os.makedirs(self.wine_prefix_path, exist_ok=True)

        proton_path = self.settings.get("PROTONPATH", DEFAULT_PROTON) if self.settings else DEFAULT_PROTON

        proc_env = os.environ.copy()
        proc_env["PROTONPATH"] = proton_path
        proc_env["WINEPREFIX"] = self.wine_prefix_path

        logger.info("Starting winetricks with PROTONPATH=%s WINEPREFIX=%s", proton_path, self.wine_prefix_path)
        subprocess.Popen([UMU_RUN_CMD, "winetricks", "--gui"], env=proc_env, start_new_session=True)

    @pyqtSlot()
    def run_regedit(self):
        """Runs regedit in the correct prefix using umu-run."""
        if not self.wine_prefix_path:
            return

        os.makedirs(self.wine_prefix_path, exist_ok=True)

        proton_path = self.settings.get("PROTONPATH", DEFAULT_PROTON) if self.settings else DEFAULT_PROTON

        proc_env = os.environ.copy()
        proc_env["PROTONPATH"] = proton_path
        proc_env["WINEPREFIX"] = self.wine_prefix_path

        logger.info("Starting regedit with PROTONPATH=%s WINEPREFIX=%s", proton_path, self.wine_prefix_path)
        subprocess.Popen([UMU_RUN_CMD, "regedit"], env=proc_env, start_new_session=True)

    def get_config(self) -> dict[str, str]:
        """
        Returns the configured environment variables as a dictionary.
        """
        config = {
            "PROTON_ENABLE_WAYLAND": "1" if self.wayland_checkbox.isChecked() else "0",
            "MANGOHUD": "1" if self.mangohud_checkbox.isChecked() else "0",
            "PROTON_USE_WOW64": "1" if self.wow64_checkbox.isChecked() else "0"
        }

        game_id = self.gameid_input.text().strip()
        if game_id:
            config["GAMEID"] = game_id

        store = self.store_combo.currentText()
        if store and store != "none":
            config["STORE"] = store

        config["PROTONPATH"] = self.protonpath_input.text().strip()

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

    def keyPressEvent(self, event) -> None:  # noqa: ANN201
        """Close the dialog when Escape is pressed."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class SelectLauncherDialog(QDialog):
    """
    A dialog to select an executable when multiple are found.
    """

    def __init__(self, target_dir: str, exe_paths: list[str], parent: QWidget | None = None):
        """Let the user choose an executable when multiple .exe files are found in a game directory.

        Args:
            target_dir: Base directory the relative exe paths are resolved against.
            exe_paths: Full filesystem paths to candidate executables.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Select Launcher")
        self.setMinimumWidth(450)
        self.exe_map = {}

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(QLabel("Multiple executables found. Please select one to launch:"))

        self.list_widget = QListWidget()
        for full_path in exe_paths:
            relative_path = relpath(full_path, target_dir)
            self.exe_map[relative_path] = full_path
            self.list_widget.addItem(relative_path)

        main_layout.addWidget(self.list_widget)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)

        self.ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)

        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        main_layout.addWidget(button_box)

    def on_selection_changed(self, current_item, previous_item):
        """Enable the OK button when a launcher item is selected."""
        self.ok_button.setEnabled(current_item is not None)

    def get_selected_launcher(self) -> str | None:
        """Return the full filesystem path of the selected executable, or None."""
        item = self.list_widget.currentItem()
        if not item:
            return None

        relative_path = item.text()
        return self.exe_map.get(relative_path)

    def keyPressEvent(self, event) -> None:  # noqa: ANN201
        """Close the dialog when Escape is pressed."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class SelectUmuIdDialog(QDialog):
    """
    A dialog to select a UMU entry when multiple match a codename.
    """

    def __init__(self, results: list[dict[str, Any]], parent: QWidget | None = None):
        """Let the user choose a UMU game entry when multiple matches are found.

        Args:
            results: List of UMU game entry dicts (with title, store, umu_id keys).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Select Game Entry")
        self.setMinimumWidth(450)
        self.results = results

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(QLabel("Multiple game entries found. Please select one:"))

        self.list_widget = QListWidget()
        for entry in self.results:
            title = entry.get('title', 'No Title')
            store = entry.get('store', 'unknown')
            umu_id = entry.get('umu_id', 'no-id')
            display_text = f"{title} ({store}) - {umu_id}"
            self.list_widget.addItem(display_text)

        main_layout.addWidget(self.list_widget)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)

        self.ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)

        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        main_layout.addWidget(button_box)

    def on_selection_changed(self, current_item, previous_item):
        """Enable the OK button when a UMU entry is selected."""
        self.ok_button.setEnabled(current_item is not None)

    def get_selected_entry(self) -> dict | None:
        """Return the full dictionary of the selected UMU game entry, or None."""
        current_row = self.list_widget.currentRow()
        if current_row < 0 or current_row >= len(self.results):
            return None
        return self.results[current_row]

    def keyPressEvent(self, event) -> None:  # noqa: ANN201
        """Close the dialog when Escape is pressed."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class SelectShortcutsDialog(QDialog):
    """
    A dialog that shows a list of .desktop files and lets the user
    select which ones to create shortcuts for (Desktop vs Application Menu).
    """

    def __init__(self, desktop_files: list[str], parent: QWidget | None = None, existing_desktop: list[str] | None = None, existing_apps: list[str] | None = None):
        """Let the user select which .desktop files get shortcuts on the Desktop and in the Application Menu.

        Args:
            desktop_files: List of .desktop file paths to present for shortcut creation.
            parent: Parent widget.
            existing_desktop: Existing desktop shortcut basenames (for pre-checking).
            existing_apps: Existing application menu shortcut basenames (for pre-checking).
        """
        super().__init__(parent)
        self.setWindowTitle("Manage Shortcuts")
        self.setMinimumWidth(500)
        self.setMinimumHeight(500)
        self.setModal(True)

        self.main_layout = QVBoxLayout(self)

        # Scroll Area
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.content_layout = QVBoxLayout(self.scroll_content)
        self.content_layout.setSpacing(2)  # Minimal spacing between checkboxes
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_area.setWidget(self.scroll_content)

        self.main_layout.addWidget(self.scroll_area)

        self.desktop_checkboxes = []
        self.apps_checkboxes = []

        # Desktop Section
        desktop_label = QLabel("<b>Desktop Shortcuts</b>")
        self.content_layout.addWidget(desktop_label)
        for file_path in desktop_files:
            name = self.parse_desktop_name(file_path)
            checkbox = QCheckBox(name)
            if existing_desktop is not None:
                checkbox.setChecked(os.path.basename(file_path) in existing_desktop)
            else:
                checkbox.setChecked(True)
            self.content_layout.addWidget(checkbox)
            self.desktop_checkboxes.append((checkbox, file_path))

        self.content_layout.addSpacing(15)

        # Application Menu Section
        apps_label = QLabel("<b>Application Menu Shortcuts</b>")
        self.content_layout.addWidget(apps_label)
        for file_path in desktop_files:
            name = self.parse_desktop_name(file_path)
            checkbox = QCheckBox(name)
            if existing_apps is not None:
                checkbox.setChecked(os.path.basename(file_path) in existing_apps)
            else:
                checkbox.setChecked(True)
            self.content_layout.addWidget(checkbox)
            self.apps_checkboxes.append((checkbox, file_path))

        # Add stretch at the end to push everything to the top
        self.content_layout.addStretch(1)

        # Global Select/Deselect
        self.select_button_layout = QHBoxLayout()
        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(self.select_all)
        self.deselect_all_button = QPushButton("Deselect All")
        self.deselect_all_button.clicked.connect(self.deselect_all)

        self.select_button_layout.addStretch(1)
        self.select_button_layout.addWidget(self.select_all_button)
        self.select_button_layout.addWidget(self.deselect_all_button)
        self.main_layout.addLayout(self.select_button_layout)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.main_layout.addWidget(self.button_box)

    @staticmethod
    def parse_desktop_name(file_path: str) -> str:
        """Read a .desktop file and extract its 'Name' entry, falling back to filename."""
        try:
            config_parser = parse_desktop_file(file_path)
            if config_parser is not None:
                return config_parser['Desktop Entry'].get('Name', os.path.basename(file_path))

        except (OSError, config_parser.Error) as e:
            logger.error("Error parsing %s for name: %s", file_path, e)

        return os.path.basename(file_path)

    def select_all(self):
        """Check all desktop and application menu checkboxes."""
        for checkbox, _ in self.desktop_checkboxes + self.apps_checkboxes:
            checkbox.setChecked(True)

    def deselect_all(self):
        """Uncheck all desktop and application menu checkboxes."""
        for checkbox, _ in self.desktop_checkboxes + self.apps_checkboxes:
            checkbox.setChecked(False)

    def get_selected_files(self) -> tuple[list[str], list[str]]:
        """Return tuples of (desktop_selected, apps_selected) lists of file paths."""
        desktop_selected = [fp for cb, fp in self.desktop_checkboxes if cb.isChecked()]
        apps_selected = [fp for cb, fp in self.apps_checkboxes if cb.isChecked()]
        return desktop_selected, apps_selected

    def keyPressEvent(self, event) -> None:  # noqa: ANN201
        """Close the dialog when Escape is pressed."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class LaunchLoadingDialog(QDialog):
    """A transient dialog shown while a game is launching via UMU.

    Displays an animated spinner with the game name and a short description.
    Closes automatically when ``wineserver`` appears (indicating UMU has
    finished initializing). Falls back to a safety timeout of 120 seconds.
    Can be dismissed early by clicking outside or pressing Escape.
    """

    _SAFETY_TIMEOUT_MS = 120_000
    _WINE_SERVER_GRACE_MS = 10_000
    _POLL_INTERVAL_MS = 500

    @staticmethod
    def _wineserver_running() -> bool:
        """Check whether any wineserver process is currently running."""
        result = subprocess.run(["pgrep", "-x", "wineserver"], capture_output=True, text=True)
        return result.returncode == 0

    def __init__(self, game_name: str, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self._game_name = game_name
        self._poll_timer: QTimer | None = None
        self._grace_timer: QTimer | None = None
        self._safety_timer: QTimer | None = None
        self._wineserver_detected = False

        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #2c3e50; border-radius: 10px;")
        self.setWindowTitle("Launching Game")
        self.setModal(True)
        self.setMinimumSize(320, 160)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Animated circular progress ring
        self.spinner = _SpinnerWidget()
        layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)

        # Game name label
        name_label = QLabel(f"Launching {game_name}…")
        name_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #ecf0f1;")
        layout.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Subtitle — updated dynamically as we wait
        self.subtitle = QLabel("Starting umu-run …")
        self.subtitle.setStyleSheet("font-size: 11px; color: #95a5a6;")
        layout.addWidget(self.subtitle, alignment=Qt.AlignmentFlag.AlignCenter)

        # Poll for wineserver every 500 ms
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll)
        self._poll_timer.start()

        # Safety timeout (120 s) in case something goes wrong
        self._safety_timer = QTimer(self)
        self._safety_timer.setSingleShot(True)
        self._safety_timer.timeout.connect(self._on_safety_timeout)
        self._safety_timer.start(self._SAFETY_TIMEOUT_MS)

        # Grace period timer — starts after wineserver is detected
        self._grace_timer = QTimer(self)
        self._grace_timer.setSingleShot(True)
        self._grace_timer.timeout.connect(self._close_now)
        self._grace_timer.stop()

        # Start spinner animation
        self.spinner.start()

    def _on_poll(self) -> None:
        """Check whether wineserver has appeared. Start grace period when it does."""
        if not self._wineserver_detected and self._wineserver_running():
            logger.info("LaunchLoadingDialog: wineserver detected — starting %d ms grace period", self._WINE_SERVER_GRACE_MS)
            self._wineserver_detected = True
            self.subtitle.setText("Game launching…")
            self._grace_timer.start(self._WINE_SERVER_GRACE_MS)
            return

        if self._wineserver_detected:
            return

        # Update subtitle to give the user a sense of progress
        phase_text = "Waiting for Wine server …"
        self.subtitle.setText(phase_text)

    def _on_safety_timeout(self) -> None:
        """Close the dialog after 120 seconds even if wineserver hasn't appeared."""
        logger.warning("LaunchLoadingDialog safety timeout reached — closing anyway.")
        self._close_now()

    def _close_now(self) -> None:
        """Stop all timers and close the dialog."""
        if self._poll_timer:
            self._poll_timer.stop()
        if self._grace_timer:
            self._grace_timer.stop()
        if self._safety_timer:
            self._safety_timer.stop()
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: ANN201
        """Close the dialog when Escape is pressed."""
        if event.key() == Qt.Key.Key_Escape:
            self._close_now()
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: Any) -> None:  # noqa: ANN401
        self._close_now()
        self.spinner.stop()
        super().closeEvent(event)


class _SpinnerWidget(QWidget):
    """A simple animated circular spinner used by LaunchLoadingDialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        self._running = True
        self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: ANN401
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = min(self.width(), self.height())
        center_x = self.width() / 2
        center_y = self.height() / 2
        radius = size * 0.38
        arc_span = 60  # degrees

        for i in range(8):
            angle = self._angle + i * 45
            rad = math.radians(angle)
            x = center_x + radius * math.cos(rad)
            y = center_y + radius * math.sin(rad)

            alpha = int(200 * (1.0 - i / 8.0))
            color = QColor("#3498db")
            color.setAlpha(alpha)
            painter.setPen(color)
            painter.setBrush(color)
            dot_radius = size * 0.04
            painter.drawEllipse(int(x - dot_radius), int(y - dot_radius),
                                int(dot_radius * 2), int(dot_radius * 2))

        painter.end()

    def _on_tick(self) -> None:
        if not self._running:
            return
        self._angle = (self._angle + 4) % 360
        self.update()
