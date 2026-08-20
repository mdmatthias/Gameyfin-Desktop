import logging
import math
import os
import subprocess
import sys
from os import getenv
from os.path import relpath
from typing import Any

from PyQt6.QtCore import pyqtSlot, QEvent, QTimer, Qt, QUrl
from PyQt6.QtGui import QPainter, QColor, QShowEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QVBoxLayout, QFormLayout, QCheckBox, QLineEdit, QPushButton, QStyle,
    QHBoxLayout, QWidget, QComboBox, QPlainTextEdit, QDialogButtonBox,
    QLabel, QDialog, QMessageBox, QListWidget, QScrollArea, QProgressBar,
)

from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.utils import parse_desktop_file, format_size
from gameyfin_frontend.config import DEFAULT_PROTON, UMU_RUN_CMD
from gameyfin_frontend.services.update_service import (
    can_auto_update,
    compare_versions,
    get_current_version,
    get_download_dir,
    get_update_asset,
    is_running_in_flatpak,
)
from gameyfin_frontend.workers import (
    FlatpakInstallWorker, UpdateCheckWorker, UpdateDownloadWorker
)

logger = logging.getLogger(__name__)

# Absolute floor for input rows, used only when the widget cannot report a
# usable height of its own.
_MIN_FIELD_HEIGHT = 34


def ensure_field_height(widget: QWidget) -> None:
    """Stop a form row from collapsing to nothing.

    Some style/stylesheet combinations (qt-material on certain platform styles)
    report a ``minimumSizeHint`` of 0 for input widgets. A form layout is then
    free to squeeze the row flat, and the field disappears — no border, no text.
    Pinning the minimum to the widget's own size hint keeps it at the height the
    theme intends, with a fixed floor for the case where the hint is unusable too.
    """
    widget.setMinimumHeight(max(widget.sizeHint().height(), _MIN_FIELD_HEIGHT))


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
        ensure_field_height(self.gameid_input)

        self.search_button = QPushButton()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.search_button.setIcon(icon)
        self.search_button.setToolTip("Search for game by name")
        button_size = max(self.gameid_input.sizeHint().height(), _MIN_FIELD_HEIGHT)
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
        ensure_field_height(self.protonpath_input)

        self.store_combo = QComboBox()
        if self.settings:
            stores = self.settings.get("GF_UMU_DB_STORES", ["none", "gog", "amazon", "battlenet", "ea", "egs",
                                                           "humble", "itchio", "steam", "ubisoft", "zoomplatform"])
        else:
            stores = ["none", "gog", "amazon", "battlenet", "ea", "egs", "humble", "itchio", "steam", "ubisoft", "zoomplatform"]
        self.store_combo.addItems(stores)
        self.store_combo.setCurrentText(default_store)
        ensure_field_height(self.store_combo)

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

    def showEvent(self, event: QShowEvent) -> None:
        """Set focus on the first input widget when the dialog opens.

        This ensures the gamepad navigator can immediately navigate the
        form fields without requiring a mouse click first.
        """
        super().showEvent(event)
        if self.wayland_checkbox.isEnabled():
            self.wayland_checkbox.setFocus()
        elif self.gameid_input.isEnabled():
            self.gameid_input.setFocus()

    @pyqtSlot()
    def search_for_game_id(self) -> None:
        """
        Opens a dialog to search for a game by title, checks ALL stores,
        and populates the umu_id and store fields from the results.
        """
        try:
            dialog = UmuSearchDialog(self.umu_database, self)
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
            logger.error("Search error: %s", e)
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


class UmuSearchDialog(QDialog):
    """
    A dialog to search for a UMU game by title and select from matching results.

    Combines the search input and results list into a single dialog so the
    search textbox can receive the same ``ensure_field_height`` treatment
    applied to other input fields in the install configuration form.

    When ``results`` is passed directly (e.g. from auto-detection), the search
    input row is hidden and the dialog acts as a pure results selector.
    """

    def __init__(self, umu_database_or_results: UmuDatabase | list[dict[str, Any]],
                 parent: QWidget | None = None):
        """Search the UMU database or select from pre-fetched results.

        Args:
            umu_database_or_results: Either a UmuDatabase instance (triggers
                search mode with a textbox) or a list of result dicts
                (results-only mode, no search input shown).
            parent: Parent widget.
        """
        super().__init__(parent)

        # Detect which mode we're in
        if isinstance(umu_database_or_results, list):
            self._results = umu_database_or_results
            self.umu_database: UmuDatabase | None = None
            self._search_mode = False
        else:
            self.umu_database = umu_database_or_results
            self._results: list[dict] = []
            self._search_mode = True

        self._selected_entry: dict | None = None
        self.setWindowTitle("Search UMU Database" if self._search_mode else "Select Game Entry")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        main_layout = QVBoxLayout(self)

        # Search row (only in search mode)
        if self._search_mode:
            search_layout = QHBoxLayout()
            self.search_input = QLineEdit()
            self.search_input.setPlaceholderText("Enter game title to search…")
            ensure_field_height(self.search_input)
            search_layout.addWidget(self.search_input)

            self.search_button = QPushButton("Search")
            search_layout.addWidget(self.search_button)
            main_layout.addLayout(search_layout)

            # Wire up search triggers
            self.search_button.clicked.connect(self._perform_search)
            self.search_input.returnPressed.connect(self._perform_search)
        else:
            self.search_input = None  # type: ignore[assignment]
            self.search_button = None  # type: ignore[assignment]

        # Results label
        self.label = QLabel()
        main_layout.addWidget(self.label)

        # Results list
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        main_layout.addWidget(self.list_widget)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        button_box.accepted.connect(self._accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        # Populate results immediately in results-only mode
        if not self._search_mode:
            self._populate_results(self._results)

    # -- search logic --------------------------------------------------------

    def _perform_search(self) -> None:
        """Run the database lookup and populate the results list."""
        search_title = self.search_input.text().strip()
        if not search_title:
            return

        self.label.setText(f"Searching for \"{search_title}\"…")
        self.list_widget.clear()
        self.ok_button.setEnabled(False)

        # search_by_partial_title is synchronous
        try:
            results = self.umu_database.search_by_partial_title(search_title)
            processed_list: list[dict] = []
            if isinstance(results, list):
                processed_list = results
            elif isinstance(results, dict) and results.get("umu_id"):
                processed_list = [results]

            all_results: list[dict] = [
                e for e in processed_list if e.get("umu_id")
            ]
            self._populate_results(all_results)
        except (ValueError, KeyError, TypeError, RuntimeError) as e:
            logger.error("Search error for title '%s': %s", search_title, e)
            self.label.setText(f"Error: {e}")

    def _populate_results(self, results: list[dict]) -> None:
        """Fill the list widget with search results."""
        if not results:
            search_title = self.search_input.text().strip()
            self.label.setText(f"No games found matching \"{search_title}\" in any store.")
            return

        for entry in results:
            title = entry.get('title', 'No Title')
            store = entry.get('store', 'unknown')
            umu_id = entry.get('umu_id', 'no-id')
            display_text = f"{title} ({store}) - {umu_id}"
            self.list_widget.addItem(display_text)

        # Auto-select when there's only one result
        if len(results) == 1:
            self.list_widget.setCurrentRow(0)
            self.label.setText(f"1 result found – auto-selected.")

    # -- selection -----------------------------------------------------------

    def _on_selection_changed(self, current, previous) -> None:  # noqa: ANN001
        """Enable OK when a result is selected."""
        self.ok_button.setEnabled(current is not None)

    def _accept(self) -> None:
        """Store the selected entry and close the dialog."""
        current_row = self.list_widget.currentRow()
        # We don't store raw results anymore; rebuild from the list items.
        # Fallback: read from the displayed text.
        item = self.list_widget.currentItem()
        if item:
            text = item.text()
            # Parse "Title (store) - umu_id" back into a dict.
            # This is a best-effort reconstruction.
            try:
                inner, umu_id = text.rsplit(' - ', 1)
                title, store = inner.rsplit(' (', 1)
                store = store.rstrip(')')
                self._selected_entry = {
                    'title': title,
                    'store': store,
                    'umu_id': umu_id,
                }
            except ValueError:
                self._selected_entry = {'title': text, 'store': 'unknown', 'umu_id': text}
        self.accept()

    def get_selected_entry(self) -> dict | None:
        """Return the full dictionary of the selected UMU game entry, or None."""
        return self._selected_entry


class SelectShortcutsDialog(QDialog):
    """
    A dialog that shows a list of .desktop files and lets the user
    select which ones to create shortcuts for (Desktop vs Application Menu).
    """

    def __init__(self, desktop_files: list[str], parent: QWidget | None = None, existing_desktop: list[str] | None = None, existing_apps: list[str] | None = None, steam_names: set[str] | None = None):
        """Let the user select which .desktop files get shortcuts on the Desktop and in the Application Menu.

        Args:
            desktop_files: List of .desktop file paths to present for shortcut creation.
            parent: Parent widget.
            existing_desktop: Existing desktop shortcut basenames (for pre-checking).
            existing_apps: Existing application menu shortcut basenames (for pre-checking).
            steam_names: Set of game display names already present in Steam — used
                         to pre-check the corresponding Steam checkboxes.
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
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Don't steal focus from checkboxes
        self.scroll_content = QWidget()
        self.scroll_content.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Don't steal focus
        self.content_layout = QVBoxLayout(self.scroll_content)
        self.content_layout.setSpacing(2)  # Minimal spacing between checkboxes
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_area.setWidget(self.scroll_content)

        self.main_layout.addWidget(self.scroll_area)

        self.desktop_checkboxes = []
        self.apps_checkboxes = []
        self.steam_checkboxes = []  # One checkbox per .desktop file

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

        self.content_layout.addSpacing(15)

        # Steam Library Section — one checkbox per .desktop file
        steam_label = QLabel("<b>Add to Steam Library</b>")
        steam_tip = QLabel(
            "When enabled, creates a non-Steam game entry in your local Steam "
            "library so you can launch the shortcut from Big Picture mode."
        )
        steam_tip.setStyleSheet("font-size: 11px; color: palette(Text);")
        steam_tip.setWordWrap(True)
        self.content_layout.addWidget(steam_label)
        self.content_layout.addWidget(steam_tip)
        for file_path in desktop_files:
            name = self.parse_desktop_name(file_path)
            checkbox = QCheckBox(name)
            # Pre-check if this shortcut's script basename is already in Steam.
            if steam_names is not None:
                script_bn = os.path.splitext(os.path.basename(file_path))[0]
                if script_bn in steam_names:
                    checkbox.setChecked(True)
            self.content_layout.addWidget(checkbox)
            self.steam_checkboxes.append((checkbox, file_path))

        # Add stretch at the end to push everything to the top
        self.content_layout.addStretch(1)

        # Global Select/Deselect (applies to all sections)
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

    def changeEvent(self, event: QEvent) -> None:  # noqa: ANN001
        """Set focus on the first checkbox after the window activates."""
        if event.type() == QEvent.Type.WindowActivate:
            if self.desktop_checkboxes:
                self.desktop_checkboxes[0][0].setFocus()
        super().changeEvent(event)

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
        """Check all desktop, app-menu, and Steam checkboxes."""
        for checkbox, _ in self.desktop_checkboxes + self.apps_checkboxes + self.steam_checkboxes:
            checkbox.setChecked(True)

    def deselect_all(self):
        """Uncheck all desktop, app-menu, and Steam checkboxes."""
        for checkbox, _ in self.desktop_checkboxes + self.apps_checkboxes + self.steam_checkboxes:
            checkbox.setChecked(False)

    def get_selected_files(self) -> tuple[list[str], list[str]]:
        """Return tuples of (desktop_selected, apps_selected) lists of file paths."""
        desktop_selected = [fp for cb, fp in self.desktop_checkboxes if cb.isChecked()]
        apps_selected = [fp for cb, fp in self.apps_checkboxes if cb.isChecked()]
        return desktop_selected, apps_selected

    def get_steam_shortcuts(self) -> list[str]:
        """Return list of .desktop file basenames the user wants added to Steam."""
        return [os.path.basename(fp) for cb, fp in self.steam_checkboxes if cb.isChecked()]


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


class UpdateDialog(QDialog):
    """Check GitHub for a newer release, then download and install it.

    Flow: checking → up-to-date / update available → downloading (with
    progress) → installing (Flatpak only) → done.  Non-Flatpak platforms
    (Windows, source Linux) see a link to the releases page instead of
    a download button.  The download button only appears for Flatpak
    installs, which can install the downloaded bundle automatically.
    """

    def __init__(self, parent: QWidget | None = None, settings: SettingsManager | None = None, release: dict | None = None):
        """Open the update dialog and immediately start the release check.

        Args:
            parent: Parent widget.
            settings: SettingsManager instance (bandwidth limit, config dir).
            release: A release dict already fetched by the caller. When given,
                the dialog skips its own check and uses this data directly.
        """
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Check for Updates")
        self.setMinimumWidth(440)

        self._state = "checking"
        self._check_worker = None
        self._download_worker = None
        self._install_worker = None
        self._release: dict | None = None
        self._asset: dict | None = None
        self._downloaded_path: str | None = None
        self._releases_url: str = ""

        main_layout = QVBoxLayout(self)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("")
        self.detail_label.setVisible(False)
        main_layout.addWidget(self.detail_label)

        self.button_box = QDialogButtonBox()
        self.ok_button = self.button_box.addButton(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = self.button_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._on_accepted)
        self.button_box.rejected.connect(self._on_rejected)
        main_layout.addWidget(self.button_box)

        self.releases_button = QPushButton("View release page on GitHub")
        self.releases_button.setVisible(False)
        self.releases_button.clicked.connect(self._open_releases_page)
        main_layout.addWidget(self.releases_button)

        self._set_state("checking")
        if release is not None:
            self._on_check_finished(release, "")
        else:
            self._start_check()

    # -- state machine -------------------------------------------------------

    def _set_state(self, state: str) -> None:
        """Update the status label buttons and progress visibility."""
        self._state = state

        if state == "checking":
            self.status_label.setText("Checking for updates…")
            self.ok_button.setText("OK")
            self.ok_button.setEnabled(False)
            self.cancel_button.setVisible(True)
            self.cancel_button.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.detail_label.setVisible(False)
            self.releases_button.setVisible(False)
        elif state == "up_to_date":
            self.ok_button.setText("OK")
            self.ok_button.setEnabled(True)
            self.ok_button.setDefault(True)
            self.cancel_button.setVisible(False)
            self.progress_bar.setVisible(False)
            self.detail_label.setVisible(False)
            self.releases_button.setVisible(False)
        elif state == "update_available":
            self.ok_button.setText("Update")
            self.ok_button.setEnabled(True)
            self.ok_button.setDefault(True)
            self.cancel_button.setVisible(True)
            self.cancel_button.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.detail_label.setVisible(False)
            self.releases_button.setVisible(False)
        elif state == "update_available_external":
            self.ok_button.setText("OK")
            self.ok_button.setEnabled(True)
            self.ok_button.setDefault(True)
            self.cancel_button.setVisible(False)
            self.progress_bar.setVisible(False)
            self.detail_label.setVisible(False)
            self.releases_button.setVisible(True)
        elif state == "downloading":
            self.ok_button.setVisible(False)
            self.cancel_button.setVisible(True)
            self.cancel_button.setEnabled(True)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
            self.detail_label.setText("")
            self.detail_label.setVisible(True)
            self.releases_button.setVisible(False)
        elif state == "installing":
            self.ok_button.setVisible(False)
            self.cancel_button.setVisible(True)
            self.cancel_button.setEnabled(False)
            self.progress_bar.setRange(0, 0)  # busy indicator
            self.progress_bar.setVisible(True)
            self.detail_label.setVisible(False)
            self.releases_button.setVisible(False)
        else:  # done / error
            self.ok_button.setVisible(True)
            self.ok_button.setText("OK")
            self.ok_button.setEnabled(True)
            self.ok_button.setDefault(True)
            self.cancel_button.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setVisible(False)
            self.detail_label.setVisible(False)
            self.releases_button.setVisible(False)

    # -- check phase ---------------------------------------------------------

    def _start_check(self) -> None:
        self._check_worker = UpdateCheckWorker()
        self._check_worker.finished.connect(self._on_check_finished)
        self._check_worker.start()

    @pyqtSlot(object, str)
    def _on_check_finished(self, release, error: str) -> None:
        if error:
            self.status_label.setText(f"Could not check for updates:\n\n{error}")
            self._set_state("error")
            return

        self._release = release
        latest = release.get("tag_name", "").lstrip("vV")
        current = get_current_version()
        if compare_versions(latest, current) <= 0:
            self.status_label.setText(
                f"You're up to date.\n\n"
                f"Current version: {current}\n"
                f"Latest release: {latest}"
            )
            self._set_state("up_to_date")
            return

        self._asset = get_update_asset(release)
        if self._asset is None:
            self.status_label.setText(
                f"Update {latest} is available, but no download suitable for "
                "this platform was found.\n\n"
                "Download it manually from the releases page."
            )
            self._set_state("error")
            return

        if not can_auto_update():
            tag = release.get("tag_name", latest)
            self._releases_url = release.get("html_url", "")
            self.status_label.setText(
                f"Update {latest} is available (current: {current})."
            )
            self._set_state("update_available_external")
            return

        self.status_label.setText(
            f"Update available: {latest} (current: {current})\n\n"
            "Download and install now?"
        )
        self._set_state("update_available")

    # -- download phase ------------------------------------------------------

    def _start_download(self) -> None:
        url = self._asset.get("browser_download_url", "")
        file_name = self._asset.get("name", "update")
        config_dir = self.settings.get_config_dir() if self.settings else os.getcwd()
        download_dir = get_download_dir(config_dir)
        os.makedirs(download_dir, exist_ok=True)
        target_path = os.path.join(download_dir, file_name)

        bandwidth_limit = 0
        if self.settings:
            try:
                bandwidth_limit = int(self.settings.get("GF_BANDWIDTH_LIMIT", 0) or 0)
            except (TypeError, ValueError):
                bandwidth_limit = 0

        self.status_label.setText(f"Downloading {file_name}…")
        self._set_state("downloading")

        self._download_worker = UpdateDownloadWorker(url, target_path, bandwidth_limit)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.bytes_received.connect(self._on_download_bytes)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    @pyqtSlot(int)
    def _on_download_progress(self, value: int) -> None:
        self.progress_bar.setValue(value)

    @pyqtSlot("long long", "long long")
    def _on_download_bytes(self, received: int, total: int) -> None:
        if total > 0:
            self.detail_label.setText(f"{format_size(received)} / {format_size(total)}")
        else:
            self.detail_label.setText(format_size(received))

    @pyqtSlot(str)
    def _on_download_finished(self, path: str) -> None:
        self._downloaded_path = path
        if is_running_in_flatpak():
            self.status_label.setText("Installing update…")
            self._set_state("installing")
            self._install_worker = FlatpakInstallWorker(path)
            self._install_worker.finished.connect(self._on_install_finished)
            self._install_worker.start()
        else:
            self.status_label.setText(
                "Update downloaded successfully.\n\n"
                f"Close the app and run {os.path.basename(path)} to finish updating."
            )
            self._set_state("done")

    @pyqtSlot(str)
    def _on_download_error(self, message: str) -> None:
        self.status_label.setText(f"Download failed:\n\n{message}")
        self._set_state("error")

    # -- install phase -------------------------------------------------------

    @pyqtSlot(bool, str)
    def _on_install_finished(self, success: bool, output: str) -> None:
        if success:
            self._remove_downloaded_file()
            self.status_label.setText(
                "Update installed successfully.\n\nPlease restart the app."
            )
            self._set_state("done")
        else:
            self.status_label.setText(
                "Installation failed:\n\n"
                f"{output or 'Unknown error'}\n\n"
                f"The file was saved to {self._downloaded_path} — "
                "you can install it manually."
            )
            self._set_state("error")

    def _remove_downloaded_file(self) -> None:
        if not self._downloaded_path:
            return
        try:
            os.remove(self._downloaded_path)
        except OSError:
            pass
        self._downloaded_path = None

    @pyqtSlot()
    def _open_releases_page(self) -> None:
        if self._releases_url:
            QDesktopServices.openUrl(QUrl(self._releases_url))

    # -- dialog buttons ------------------------------------------------------

    @pyqtSlot()
    def _on_accepted(self) -> None:
        if self._state == "update_available":
            self._start_download()
        elif self._state == "update_available_external" and self._releases_url:
            QDesktopServices.openUrl(QUrl(self._releases_url))
            self.accept()
        else:
            self.accept()

    @pyqtSlot()
    def _on_rejected(self) -> None:
        if self._state == "downloading" and self._download_worker:
            self._download_worker.stop()
        self._cleanup_workers()
        self.reject()

    def _cleanup_workers(self) -> None:
        for worker in (self._check_worker, self._download_worker, self._install_worker):
            if worker is not None:
                worker.wait(3000)
        self._check_worker = None
        self._download_worker = None
        self._install_worker = None

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._state == "downloading" and self._download_worker:
            self._download_worker.stop()
        self._cleanup_workers()
        super().closeEvent(event)
