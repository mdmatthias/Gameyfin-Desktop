import glob
import logging
import os
from typing import Any

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                             QHBoxLayout, QLabel, QMessageBox, QDialog, QComboBox, QFileDialog,
                             QDialogButtonBox, QLineEdit, QAbstractItemView, QScrollArea)
from PyQt6.QtCore import Qt, QProcess

from gameyfin_frontend.dialogs import InstallConfigDialog, LaunchLoadingDialog, ensure_field_height
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import SettingsManager
from gameyfin_frontend.services import PrefixService, ShortcutService, SteamIntegrationService
from gameyfin_frontend.services.game_launcher import log_output_as_it_arrives
from gameyfin_frontend.config import DEFAULT_PROTON
from gameyfin_frontend.utils import build_umu_env_prefix, shell_dquote

logger = logging.getLogger(__name__)


class PrefixItemWidget(QWidget):
    def __init__(self, prefix_name: str, prefix_path: str, umu_database: UmuDatabase, parent: QWidget | None = None, settings: SettingsManager | None = None):
        """Create a prefix item widget with name, script launcher, and shortcut management.

        Args:
            prefix_name: Display name of the prefix (e.g. "dark-earth").
            prefix_path: Full filesystem path to the Wine prefix directory.
            umu_database: UmuDatabase instance for UMU lookups.
            parent: Parent widget.
            settings: SettingsManager instance providing app configuration.
        """
        super().__init__(parent)
        self.prefix_name = prefix_name
        self.prefix_path = prefix_path
        self.umu_database = umu_database
        self.settings = settings
        self._loading_dialog = None
        self._script_process: QProcess | None = None
        self._exe_process: QProcess | None = None

        # Determine scripts_dir based on prefix_name
        game_name = prefix_name.removesuffix("_pfx")
        self.scripts_dirs = settings.get_shortcuts_dirs(game_name) if settings else []
        self.primary_scripts_dir = settings.get_shortcuts_dir(game_name) if settings else ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.name_label = QLabel(prefix_name)
        layout.addWidget(self.name_label)

        layout.addStretch()

        self.run_exe_btn = QPushButton("Run exe on prefix")
        self.run_exe_btn.setFixedWidth(160)
        self.run_exe_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.run_exe_btn.clicked.connect(self.run_exe_on_prefix)
        layout.addWidget(self.run_exe_btn)

        self.script_combo = QComboBox()
        self.script_combo.setFixedWidth(300)
        self.script_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.script_combo.activated.connect(self.launch_script)
        layout.addWidget(self.script_combo)

        self.manage_combo = QComboBox()
        self.manage_combo.setFixedWidth(180)
        self.manage_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.manage_combo.addItem("Manage")
        self.manage_combo.addItem("Shortcuts")
        self.manage_combo.addItem("Config")
        self.manage_combo.addItem("Delete")
        self.manage_combo.activated.connect(self.manage_activated)

        layout.addWidget(self.manage_combo)

        # Match the downloads page's button height - QComboBox renders
        # noticeably shorter than QPushButton under themes like qt-material.
        control_height = QPushButton().sizeHint().height()
        self.run_exe_btn.setFixedHeight(control_height)
        self.script_combo.setFixedHeight(control_height)
        self.manage_combo.setFixedHeight(control_height)

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

                process = QProcess(self)
                process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
                process.setWorkingDirectory(os.path.dirname(script_path))
                process.start(script_path, [])
                if not process.waitForStarted():
                    raise OSError(f"Failed to start {script_path}")
                log_output_as_it_arrives(process)
                self._script_process = process

                # Reset to placeholder
                self.script_combo.setCurrentIndex(0)
            except OSError as e:
                logger.error("Failed to launch script %s: %s", script_path, e)
                QMessageBox.critical(self, "Launch Error", f"Failed to launch: {e}")

    def run_exe_on_prefix(self) -> None:
        """Pick a Windows executable and run it inside this prefix via umu-run.

        Offers to keep it as an extra shortcut before launching, so the answer
        isn't waited on behind a game that may run for hours.
        """
        start_dir = os.path.join(self.prefix_path, "drive_c")
        if not os.path.isdir(start_dir):
            start_dir = self.prefix_path

        exe_path, _ = QFileDialog.getOpenFileName(
            self, "Select executable to run in prefix", start_dir,
            "Windows executables (*.exe *.bat *.msi);;All Files (*)",
        )
        if not exe_path:
            return

        self.prompt_add_exe_as_shortcut(exe_path)

        config: dict[str, Any] = {}
        if self.settings:
            config, _ = PrefixService(self.settings).load_config_from_scripts_dir(
                self.prefix_name.removesuffix("_pfx")
            )

        proton_path = config.get("PROTONPATH") or (self.settings.get("PROTONPATH") if self.settings else "") or DEFAULT_PROTON
        env_prefix = build_umu_env_prefix(proton_path, self.prefix_path, config)
        command = f'{env_prefix}exec umu-run {shell_dquote(exe_path)}'

        exe_name = os.path.basename(exe_path)
        try:
            self._loading_dialog = LaunchLoadingDialog(exe_name, parent=self)
            self._loading_dialog.show()

            logger.info('Running exe in prefix %s: /bin/sh -c "%s"', self.prefix_name, command)
            process = QProcess(self)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.setWorkingDirectory(os.path.dirname(exe_path))
            process.start("/bin/sh", ["-c", command])
            if not process.waitForStarted():
                raise OSError(f"Failed to start {exe_path}")
            log_output_as_it_arrives(process)
            self._exe_process = process
        except OSError as e:
            logger.error("Failed to run exe %s: %s", exe_path, e)
            QMessageBox.critical(self, "Launch Error", f"Failed to launch: {e}")

    def prompt_add_exe_as_shortcut(self, exe_path: str) -> None:
        """Ask whether to keep an executable as an extra shortcut for this prefix.

        Args:
            exe_path: Full path to the executable about to be run.
        """
        if not self.settings:
            return

        answer = QMessageBox.question(
            self,
            "Add as Shortcut?",
            f"Also add '{os.path.basename(exe_path)}' as an extra shortcut for this prefix?\n\n"
            "It will then be available in the script selector, and in the shortcut "
            "manager for placing on your desktop, in your app menu or in Steam.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        default_name = os.path.splitext(os.path.basename(exe_path))[0]
        name = self.ask_script_name(default_name)
        if not name:
            return

        game_name = self.prefix_name.removesuffix("_pfx")
        try:
            _desktop_path, script_path = PrefixService(self.settings).create_shortcut_for_exe(
                self.prefix_path, game_name, exe_path, name,
            )
        except OSError as e:
            logger.error("Failed to create shortcut for %s: %s", exe_path, e)
            QMessageBox.critical(self, "Error", f"Failed to create shortcut:\n{e}")
            return

        self.populate_scripts()
        logger.info("Added shortcut script %s", script_path)

    def ask_script_name(self, default_name: str) -> str | None:
        """Ask for a name for a new script.

        QInputDialog is avoided here: its built-in line edit reports a zero
        minimum size hint under qt-material, so the field collapses and the
        dialog shows only a label and buttons.

        Args:
            default_name: Name to pre-fill the field with.

        Returns:
            The entered name, or ``None`` if the dialog was cancelled or left empty.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Shortcut Name")
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Name for the new shortcut:"))

        name_edit = QLineEdit(default_name)
        ensure_field_height(name_edit)
        name_edit.selectAll()
        layout.addWidget(name_edit)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        name_edit.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return name_edit.text().strip() or None

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

        steam_service = SteamIntegrationService(self.settings)
        shortcut_service = ShortcutService(self.settings, steam_service=steam_service)
        existing_desktop, existing_apps = shortcut_service.detect_existing_shortcuts(desktop_files)

        game_name = self.prefix_name.removesuffix("_pfx")
        logger.info("Opening shortcut dialog for %d .desktop files (Steam service=%s)", len(desktop_files), steam_service)
        selection = shortcut_service.show_shortcut_dialog(
            desktop_files, self,
            existing_desktop=existing_desktop,
            existing_apps=existing_apps,
            game_name=game_name,
        )
        if selection is None:
            logger.info("Shortcut dialog cancelled.")
            return

        selected_desktop, selected_apps, steam_shortcuts = selection
        logger.info("Dialog accepted — desktop=%d apps=%d steam=%d", len(selected_desktop), len(selected_apps), len(steam_shortcuts))
        game_name = self.prefix_name.removesuffix("_pfx")
        success = shortcut_service.create_shortcuts_for_prefix(
            self.prefix_path, game_name,
            selected_desktop, selected_apps, self,
            steam_shortcuts=steam_shortcuts,
        )
        if success:
            self.populate_scripts()
            QMessageBox.information(self, "Shortcuts Updated", "Shortcuts have been updated.")

    def configure_prefix(self) -> None:
        """Open the install config dialog for this prefix."""
        if not self.settings:
            return

        prefix_service = PrefixService(self.settings)
        game_name = self.prefix_name.removesuffix("_pfx")
        initial_config, _ = prefix_service.load_config_from_scripts_dir(game_name)

        # Collect available scripts for per-script configuration
        scripts = []
        for sd in self.scripts_dirs:
            if os.path.exists(sd):
                scripts.extend(glob.glob(os.path.join(sd, "*.sh")))
        scripts.sort()

        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self,
            wine_prefix_path=self.prefix_path,
            initial_config=initial_config,
            scripts=scripts,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_config = dialog.get_config()
            game_name = self.prefix_name.removesuffix("_pfx")
            try:
                prefix_service.save_config(game_name, new_config)
            except OSError as e:
                QMessageBox.warning(self, "Save Error", f"Failed to save config: {e}")
                return

            count = prefix_service.update_scripts(
                self.prefix_path, new_config, game_name,
            )
            if count > 0:
                QMessageBox.information(self, "Scripts Updated", f"Updated {count} shortcut script(s) with new configuration.")
            else:
                QMessageBox.information(self, "No Scripts Updated", "No suitable .sh scripts found to update.")

    def manage_activated(self, index: int) -> None:
        """Route manage combobox selection to the appropriate action.

        Args:
            index: The combobox index (0=Manage, 1=Shortcuts, 2=Config, 3=Delete).
        """
        # Reset to placeholder after selection
        self.manage_combo.setCurrentIndex(0)

        if index == 1:
            self.recreate_shortcuts()
        elif index == 2:
            self.configure_prefix()
        elif index == 3:
            self.delete_prefix()

    def delete_prefix(self) -> None:
        """Delete this prefix and its associated shortcut scripts after confirmation."""
        if not self.settings:
            return

        prefix_service = PrefixService(self.settings)

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the prefix '{self.prefix_name}'?\n\n"
            f"Path: {self.prefix_path}\n\n"
            "\u26a0\ufe0f NOTE: Prefixes often contain your saved games. If you delete this prefix, you will LOSE ALL SAVE DATA for this game!\n\n"
            "This action cannot be undone. Do you wish to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if confirm == QMessageBox.StandardButton.Yes:
            try:
                prefix_service.delete_prefix(self.prefix_path, self.prefix_name)
                parent = self.parentWidget()
                if isinstance(parent, QListWidget):
                    parent = parent.parentWidget()
                if isinstance(parent, PrefixManagerWidget):
                    parent.refresh_prefixes()
            except (OSError, IOError) as e:
                QMessageBox.critical(self, "Error", f"Failed to delete prefix:\n{e}")


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
        """Build the UI layout: header with refresh button and prefix list."""
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("Installed Games")
        header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        header_layout.addWidget(header_label)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_btn.clicked.connect(self.refresh_prefixes)
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # List (scrollable)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.list_widget = QListWidget(self.scroll_content)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # qt-material's QListView::item padding otherwise insets each row's
        # itemWidget below the size we gave it via setSizeHint(), clipping it.
        self.list_widget.setStyleSheet("QListView::item { padding: 0px; }")

        self.scroll_area.setWidget(self.scroll_content)
        scroll_layout = QVBoxLayout(self.scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addWidget(self.list_widget)

        layout.addWidget(self.scroll_area)

        # Wire explicit tab order for keyboard/gamepad navigation
        self._tab_order_chain: list[tuple[QWidget, QWidget]] = []
        self._wire_tab_order()

    def refresh_theme_sizing(self) -> None:
        """Resync every row's fixed sizes after the active theme changes at runtime."""
        self.refresh_prefixes()

    def _wire_tab_order(self) -> None:
        """Wire setTabOrder chain: Refresh → List."""
        widgets = [self.refresh_btn, self.list_widget]
        for i in range(len(widgets) - 1):
            pair = (widgets[i], widgets[i + 1])
            if pair not in self._tab_order_chain:
                self._tab_order_chain.append(pair)
                QWidget.setTabOrder(widgets[i], widgets[i + 1])

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

                widget = PrefixItemWidget(p, prefix_path, self.umu_database, settings=self.settings)
                item.setSizeHint(widget.sizeHint())

                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, widget)

        except OSError as e:
            logger.error("Error reading prefixes: %s", e)
