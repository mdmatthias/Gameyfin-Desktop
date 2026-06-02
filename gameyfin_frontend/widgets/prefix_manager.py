import glob
import logging
import os
import subprocess
from typing import Any

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QListWidget, QPushButton,
                             QHBoxLayout, QLabel, QMessageBox, QDialog, QComboBox, QListWidgetItem)
from PyQt6.QtCore import Qt

from gameyfin_frontend.dialogs import InstallConfigDialog, LaunchLoadingDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.services import PrefixService, ShortcutService

logger = logging.getLogger(__name__)


class PrefixItemWidget(QWidget):
    def __init__(self, prefix_name: str, prefix_path: str, parent: QWidget | None = None, settings: SettingsManager | None = None):
        """Create a prefix item widget with name, script launcher, and shortcut management.

        Args:
            prefix_name: Display name of the prefix (e.g. "dark-earth").
            prefix_path: Full filesystem path to the Wine prefix directory.
            parent: Parent widget.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__(parent)
        self.prefix_name = prefix_name
        self.prefix_path = prefix_path
        self.settings = settings
        self._loading_dialog = None

        # Determine scripts_dir based on prefix_name
        game_name = prefix_name.removesuffix("_pfx")
        self.scripts_dirs = settings.get_shortcuts_dirs(game_name) if settings else []
        self.primary_scripts_dir = settings.get_shortcuts_dir(game_name) if settings else ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.name_label = QLabel(prefix_name)
        layout.addWidget(self.name_label)

        layout.addStretch()

        self.recreate_btn = QPushButton("Manage shortcuts")
        self.recreate_btn.setFixedWidth(180)
        self.recreate_btn.clicked.connect(self.recreate_shortcuts)
        layout.addWidget(self.recreate_btn)

        self.script_combo = QComboBox()
        self.script_combo.setFixedWidth(300)
        self.script_combo.activated.connect(self.launch_script)
        layout.addWidget(self.script_combo)

        self.populate_scripts()

    def populate_scripts(self) -> None:
        """Populate the script combo box with available .sh scripts for this prefix."""
        self.script_combo.clear()
        # Collect scripts from both new and legacy locations
        scripts = []
        for sd in self.scripts_dirs:
            if os.path.exists(sd):
                scripts.extend(glob.glob(os.path.join(sd, "*.sh")))
        scripts.sort()

        if not scripts:
            self.script_combo.addItem("No scripts found")
            self.script_combo.setEnabled(False)
        else:
            self.script_combo.addItem("Select script to launch...")
            for s in scripts:
                self.script_combo.addItem(os.path.basename(s), s)

    def launch_script(self, index: int) -> None:
        """Launch the selected script via subprocess and reset the combo box.

        Shows a loading dialog with the script name while Proton initializes.

        Args:
            index: The combo box index of the selected script.
        """
        # Skip the placeholder at index 0
        if index == 0:
            return

        script_path = self.script_combo.itemData(index)
        if script_path:
            try:
                # Use the script filename (without .sh) as the display name
                script_name = os.path.splitext(os.path.basename(script_path))[0]

                # Show loading dialog before launching (keep reference to prevent GC)
                self._loading_dialog = LaunchLoadingDialog(script_name, parent=self)
                self._loading_dialog.show()

                subprocess.Popen([script_path], cwd=os.path.dirname(script_path),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # Reset to placeholder
                self.script_combo.setCurrentIndex(0)
            except OSError as e:
                logger.error("Failed to launch script %s: %s", script_path, e)
                QMessageBox.critical(self, "Launch Error", f"Failed to launch: {e}")

    def recreate_shortcuts(self) -> None:
        """Open the shortcut selection dialog and recreate desktop shortcuts for this prefix."""
        shortcuts_dir = os.path.join(self.prefix_path, "drive_c", "proton_shortcuts")
        if not os.path.isdir(shortcuts_dir):
            QMessageBox.warning(self, "No Shortcuts Found",
                                f"The directory '{shortcuts_dir}' does not exist.\n\n"
                                "Shortcuts are usually captured during the installation process.")
            return

        desktop_files = glob.glob(os.path.join(shortcuts_dir, "*.desktop"))
        if not desktop_files:
            QMessageBox.warning(self, "No Shortcuts Found", "No .desktop files found in the proton_shortcuts directory.")
            return

        shortcut_service = ShortcutService(self.settings)
        existing_desktop, existing_apps = shortcut_service.detect_existing_shortcuts(desktop_files)

        selection = shortcut_service.show_shortcut_dialog(
            desktop_files, self,
            existing_desktop=existing_desktop,
            existing_apps=existing_apps,
        )
        if selection is None:
            return

        selected_desktop, selected_apps = selection
        game_name = self.prefix_name.removesuffix("_pfx")
        success = shortcut_service.create_shortcuts_for_prefix(
            self.prefix_path, game_name,
            selected_desktop, selected_apps, self,
        )
        if success:
            self.populate_scripts()
            QMessageBox.information(self, "Shortcuts Updated", "Shortcuts have been updated.")


class PrefixManagerWidget(QWidget):
    def __init__(self, umu_database: UmuDatabase, parent: QWidget | None = None, settings: SettingsManager | None = None):
        """Create the prefix manager widget with a list of installed game prefixes.

        Args:
            umu_database: UmuDatabase instance for UMU lookups.
            parent: Parent widget.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__(parent)
        self.umu_database = umu_database
        self.settings = settings
        self.prefixes_dir = settings.get_prefixes_dir() if settings else ""
        self.prefix_service = PrefixService(settings) if settings else None

        self.init_ui()
        self.refresh_prefixes()

    def init_ui(self) -> None:
        """Build the UI layout: header with refresh button, prefix list, and action buttons."""
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("Installed Games")
        header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        header_layout.addWidget(header_label)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.clicked.connect(self.refresh_prefixes)
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # List
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemDoubleClicked.connect(self.open_selected_prefix_config)
        layout.addWidget(self.list_widget)

        # Buttons
        btn_layout = QHBoxLayout()
        self.config_btn = QPushButton("Configure Prefix")
        self.config_btn.clicked.connect(self.open_selected_prefix_config)
        self.config_btn.setEnabled(False)

        self.delete_btn = QPushButton("Delete Prefix")
        self.delete_btn.setStyleSheet("background-color: #d9534f; color: white;")  # Bootstrap danger colorish
        self.delete_btn.clicked.connect(self.delete_selected_prefix)
        self.delete_btn.setEnabled(False)

        btn_layout.addWidget(self.config_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.delete_btn)

        layout.addLayout(btn_layout)

        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)

    def refresh_prefixes(self) -> None:
        """Scan the prefix directories and rebuild the list widget with prefix items."""
        self.list_widget.clear()
        # Ensure the new prefixes dir exists
        if not os.path.exists(self.prefixes_dir):
            try:
                os.makedirs(self.prefixes_dir, exist_ok=True)
            except OSError:
                return

        if not self.prefix_service:
            return

        try:
            all_prefixes = self.prefix_service.get_all_prefixes()
            prefixes = sorted(all_prefixes.keys())

            for p in prefixes:
                prefix_path = all_prefixes[p]
                game_name = p
                if game_name.endswith("_pfx"):
                    game_name = game_name[:-4]

                item = QListWidgetItem(self.list_widget)
                item.setData(Qt.ItemDataRole.UserRole, p)
                # Store the actual prefix path so delete/open know where it lives
                item.setData(Qt.ItemDataRole.UserRole + 1, prefix_path)

                widget = PrefixItemWidget(p, prefix_path, settings=self.settings)
                item.setSizeHint(widget.sizeHint())

                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, widget)

        except OSError as e:
            logger.error("Error reading prefixes: %s", e)

    def on_selection_changed(self) -> None:
        """Enable/disable config and delete buttons based on list selection."""
        has_selection = len(self.list_widget.selectedItems()) > 0
        self.config_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)

    def open_selected_prefix_config(self) -> None:
        """Open the install config dialog for the selected prefix, save changes, and update scripts.

        Loads existing config from config.json or extracts it from .sh scripts if unavailable.
        """
        item = self.list_widget.currentItem()
        if not item or not self.prefix_service:
            return

        prefix_name = item.data(Qt.ItemDataRole.UserRole)
        # Use stored path (may be from legacy dir) instead of constructing from prefixes_dir
        prefix_path = item.data(Qt.ItemDataRole.UserRole + 1)
        if prefix_path is None:
            prefix_path = os.path.join(self.prefixes_dir, prefix_name)

        # Derive game name
        game_name = prefix_name
        if game_name.endswith("_pfx"):
            game_name = game_name[:-4]  # Remove _pfx

        # Load existing config if available
        initial_config, _scripts_dir = self.prefix_service.load_config_from_scripts_dir(game_name)

        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self,
            wine_prefix_path=prefix_path,
            initial_config=initial_config
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_config = dialog.get_config()

            # Save config to the primary (new) scripts dir
            try:
                self.prefix_service.save_config(game_name, new_config)
            except OSError as e:
                QMessageBox.warning(self, "Save Error", f"Failed to save config: {e}")
                return

            # Update scripts in the primary dir
            count = self.prefix_service.update_scripts(
                prefix_path, new_config, game_name
            )
            if count > 0:
                QMessageBox.information(self, "Scripts Updated", f"Updated {count} shortcut script(s) with new configuration.")
            else:
                QMessageBox.information(self, "No Scripts Updated", "No suitable .sh scripts found to update.")

    def delete_selected_prefix(self) -> None:
        """Delete the selected prefix and its associated shortcut scripts after confirmation.

        Prompts the user with a warning about losing save data before deleting.
        """
        item = self.list_widget.currentItem()
        if not item or not self.prefix_service:
            return

        prefix_name = item.data(Qt.ItemDataRole.UserRole)
        # Use stored path (may be from legacy dir) instead of constructing from prefixes_dir
        prefix_path = item.data(Qt.ItemDataRole.UserRole + 1)
        if prefix_path is None:
            prefix_path = os.path.join(self.prefixes_dir, prefix_name)

        # Derive game name
        game_name = prefix_name
        if game_name.endswith("_pfx"):
            game_name = game_name[:-4]

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the prefix '{prefix_name}'?\n\n"
            f"Path: {prefix_path}\n\n"
            "\u26a0\ufe0f NOTE: Prefixes often contain your saved games. If you delete this prefix, you will LOSE ALL SAVE DATA for this game!\n\n"
            "This action cannot be undone. Do you wish to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if confirm == QMessageBox.StandardButton.Yes:
            try:
                self.prefix_service.delete_prefix(prefix_path, game_name)
                self.refresh_prefixes()
            except (OSError, IOError) as e:
                QMessageBox.critical(self, "Error", f"Failed to delete prefix:\n{e}")
