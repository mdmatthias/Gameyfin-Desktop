import configparser
import os
import sys  # <-- Added
from os import getenv
from os.path import relpath

from PyQt6.QtCore import pyqtSlot, QProcess  # <-- Added QProcess
from PyQt6.QtWidgets import QVBoxLayout, QFormLayout, QCheckBox, QLineEdit, QPushButton, QStyle, QHBoxLayout, QWidget, \
    QComboBox, QPlainTextEdit, QDialogButtonBox, QLabel, QInputDialog, QDialog, QMessageBox, QListWidget, QScrollArea

from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import settings_manager


class InstallConfigDialog(QDialog):
    """
    A dialog to configure environment variables before installation.
    """

    def __init__(self, umu_database: UmuDatabase, parent=None,
                 default_game_id="umu-default", default_store="none",
                 wine_prefix_path: str = None, initial_config: dict = None):
        super().__init__(parent)
        self.umu_database = umu_database
        self.wine_prefix_path = wine_prefix_path
        self.setWindowTitle("Installation Configuration")
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.wayland_checkbox = QCheckBox("Enable Wayland support")

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

        self.store_combo = QComboBox()
        stores = settings_manager.get("GF_UMU_DB_STORES", ["none", "gog", "amazon", "battlenet", "ea", "egs",
                                                           "humble", "itchio", "steam", "ubisoft", "zoomplatform"])
        self.store_combo.addItems(stores)
        self.store_combo.setCurrentText(default_store)

        self.extra_vars_input = QPlainTextEdit()
        self.extra_vars_input.setPlaceholderText("KEY1=VALUE1\nKEY2=VALUE2")
        
        # Apply initial config if provided
        if initial_config:
            if initial_config.get("PROTON_ENABLE_WAYLAND") == "1":
                self.wayland_checkbox.setChecked(True)
            
            if "GAMEID" in initial_config:
                self.gameid_input.setText(initial_config["GAMEID"])
            
            if "STORE" in initial_config:
                self.store_combo.setCurrentText(initial_config["STORE"])
                
            # Populate extra vars
            extra_lines = []
            for k, v in initial_config.items():
                if k not in ["PROTON_ENABLE_WAYLAND", "GAMEID", "STORE"]:
                    extra_lines.append(f"{k}={v}")
            self.extra_vars_input.setPlainText("\n".join(extra_lines))

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        main_layout.addWidget(self.wayland_checkbox)

        form_layout.addRow("Umu protonfix:", self.gameid_widget)
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

        wine_tools_layout.addWidget(self.winecfg_button)
        wine_tools_layout.addWidget(self.winetricks_button)

        main_layout.addWidget(self.wine_tools_widget)

        main_layout.addWidget(button_box)

        self.winecfg_button.clicked.connect(self.run_winecfg)
        self.winetricks_button.clicked.connect(self.run_winetricks)
        self.search_button.clicked.connect(self.search_for_game_id)

    @pyqtSlot()
    def search_for_game_id(self):
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
            print(f"Searching all stores for title: {search_title}...")

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

        except Exception as e:
            QMessageBox.warning(self, "Search Error", f"An error occurred during search:\n{e}")

    @pyqtSlot()
    def run_winecfg(self):
        """Runs winecfg in the correct prefix using umu-run."""
        if not self.wine_prefix_path:
            return

        os.makedirs(self.wine_prefix_path, exist_ok=True)

        proton_path = settings_manager.get("PROTONPATH", "GE-Proton")
        env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{self.wine_prefix_path}\" "

        command_string = f"{env_prefix} umu-run winecfg"

        print(f"Executing: /bin/sh -c \"{command_string}\"")
        QProcess.startDetached("/bin/sh", ["-c", command_string])

    @pyqtSlot()
    def run_winetricks(self):
        """Runs winetricks in the correct prefix."""
        if not self.wine_prefix_path:
            return

        os.makedirs(self.wine_prefix_path, exist_ok=True)

        proton_path = settings_manager.get("PROTONPATH", "GE-Proton")
        env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{self.wine_prefix_path}\" "

        command_string = f"{env_prefix} winetricks"

        print(f"Executing: /bin/sh -c \"{command_string}\"")
        QProcess.startDetached("/bin/sh", ["-c", command_string])

    def get_config(self) -> dict:
        """
        Returns the configured environment variables as a dictionary.
        """
        config = {"PROTON_ENABLE_WAYLAND": "1" if self.wayland_checkbox.isChecked() else "0"}

        game_id = self.gameid_input.text().strip()
        if game_id:
            config["GAMEID"] = game_id

        store = self.store_combo.currentText()
        if store and store != "none":
            config["STORE"] = store

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
        """Enables the OK button when an item is selected."""
        self.ok_button.setEnabled(current_item is not None)

    def get_selected_launcher(self) -> str | None:
        """Returns the full path of the selected executable."""
        item = self.list_widget.currentItem()
        if not item:
            return None

        relative_path = item.text()
        return self.exe_map.get(relative_path)


class SelectUmuIdDialog(QDialog):
    """
    A dialog to select a UMU entry when multiple match a codename.
    """

    def __init__(self, results: list[dict], parent=None):
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
        """Enables the OK button when an item is selected."""
        self.ok_button.setEnabled(current_item is not None)

    def get_selected_entry(self) -> dict | None:
        """Returns the full dictionary of the selected entry."""
        current_row = self.list_widget.currentRow()
        if current_row < 0 or current_row >= len(self.results):
            return None
        return self.results[current_row]


class SelectShortcutsDialog(QDialog):
    """
    A dialog that shows a list of .desktop files and lets the user
    select which ones to create shortcuts for.
    """

    def __init__(self, desktop_files: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Shortcuts")
        self.setMinimumWidth(400)
        self.setModal(True)

        self.main_layout = QVBoxLayout(self)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.checkbox_layout = QVBoxLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)

        self.main_layout.addWidget(QLabel("Select which shortcuts to create:"))
        self.main_layout.addWidget(self.scroll_area)

        self.checkboxes = []

        for file_path in desktop_files:
            name = self.parse_desktop_name(file_path)
            checkbox = QCheckBox(name)
            checkbox.setChecked(True)
            self.checkbox_layout.addWidget(checkbox)
            self.checkboxes.append((checkbox, file_path))

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
        """Reads a .desktop file and gets its 'Name' entry."""
        try:
            # configparser needs a section header
            with open(file_path, 'r') as f:
                content = f.read()
            if not content.strip().startswith('[Desktop Entry]'):
                content = '[Desktop Entry]\n' + content

            config_parser = configparser.ConfigParser(strict=False)
            config_parser.optionxform = str
            config_parser.read_string(content)

            if 'Desktop Entry' in config_parser:
                return config_parser['Desktop Entry'].get('Name', os.path.basename(file_path))

        except Exception as e:
            print(f"Error parsing {file_path} for name: {e}")

        return os.path.basename(file_path)  # Fallback

    def select_all(self):
        for checkbox, _ in self.checkboxes:
            checkbox.setChecked(True)

    def deselect_all(self):
        for checkbox, _ in self.checkboxes:
            checkbox.setChecked(False)

    def get_selected_files(self) -> list:
        """Returns a list of file paths for the checked items."""
        selected = []
        for checkbox, file_path in self.checkboxes:
            if checkbox.isChecked():
                selected.append(file_path)
        return selected