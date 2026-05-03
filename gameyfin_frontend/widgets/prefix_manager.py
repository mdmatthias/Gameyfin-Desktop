import glob
import json
import logging
import os
import re
import subprocess

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QListWidget, QPushButton,
                             QHBoxLayout, QLabel, QMessageBox, QDialog, QComboBox, QListWidgetItem, QCheckBox)
from PyQt6.QtCore import Qt
from gameyfin_frontend.dialogs import InstallConfigDialog, SelectShortcutsDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import settings_manager
from gameyfin_frontend.utils import (
    get_xdg_user_dir, create_shortcuts, build_umu_env_prefix
)

logger = logging.getLogger(__name__)

class PrefixItemWidget(QWidget):
    def __init__(self, prefix_name, prefix_path, parent=None):
        super().__init__(parent)
        self.prefix_name = prefix_name
        self.prefix_path = prefix_path

        # Determine scripts_dir based on prefix_name
        game_name = prefix_name.removesuffix("_pfx")
        self.scripts_dirs = settings_manager.get_shortcuts_dirs(game_name)
        self.primary_scripts_dir = settings_manager.get_shortcuts_dir(game_name)

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


    def populate_scripts(self):
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

    def launch_script(self, index):
        # Skip the placeholder at index 0
        if index == 0:
            return
            
        script_path = self.script_combo.itemData(index)
        if script_path:
            try:
                subprocess.Popen([script_path], cwd=os.path.dirname(script_path),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # Reset to placeholder
                self.script_combo.setCurrentIndex(0)
            except OSError as e:
                logger.error("Failed to launch script %s: %s", script_path, e)
                QMessageBox.critical(self, "Launch Error", f"Failed to launch: {e}")

    def recreate_shortcuts(self):
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

        # Detect existing shortcuts for Desktop and Applications separately
        existing_desktop = []
        existing_apps = []
        home_dir = os.path.expanduser("~")
        desktop_dir = os.path.join(home_dir, get_xdg_user_dir("DESKTOP"))
        apps_dir = os.path.join(home_dir, ".local", "share", "applications")
        
        for df in desktop_files:
            bn = os.path.basename(df)
            if os.path.exists(os.path.join(desktop_dir, bn)):
                existing_desktop.append(bn)
            if os.path.exists(os.path.join(apps_dir, bn)):
                existing_apps.append(bn)

        dialog = SelectShortcutsDialog(desktop_files, self, 
                                      existing_desktop=existing_desktop, 
                                      existing_apps=existing_apps)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_desktop, selected_apps = dialog.get_selected_files()
            self.create_desktop_shortcuts(desktop_files, selected_desktop, selected_apps)
            self.populate_scripts()
            QMessageBox.information(self, "Shortcuts Updated", "Shortcuts have been updated.")

    def create_desktop_shortcuts(self, all_desktop_files: list, selected_desktop: list, selected_apps: list):
        # Load config.json if available (check both new and legacy locations)
        prefix_basename = os.path.basename(self.prefix_path)
        game_name = prefix_basename.removesuffix("_pfx")
        scripts_dir = settings_manager.get_shortcuts_dir(game_name)

        install_config = {}
        for sd in settings_manager.get_shortcuts_dirs(game_name):
            config_path = os.path.join(sd, "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as f:
                        install_config = json.load(f)
                    break
                except (json.JSONDecodeError, OSError) as e:
                    logger.error("Error loading config for shortcuts: %s", e)

        proton_path = install_config.get("PROTONPATH") or settings_manager.get("PROTONPATH") or "GE-Proton"

        create_shortcuts(
            all_desktop_files=all_desktop_files,
            scripts_dir=self.primary_scripts_dir,
            wine_prefix=self.prefix_path,
            install_config=install_config,
            proton_path=proton_path,
            selected_desktop=selected_desktop,
            selected_apps=selected_apps,
            remove_unselected=True,
        )


class PrefixManagerWidget(QWidget):
    def __init__(self, umu_database: UmuDatabase, parent=None):
        super().__init__(parent)
        self.umu_database = umu_database
        self.prefixes_dir = settings_manager.get_prefixes_dir()
        
        self.init_ui()
        self.refresh_prefixes()

    def _get_all_prefixes(self):
        """Collect prefix directories from all configured prefix dirs (new + legacy)."""
        result = {}
        for prefix_base in settings_manager.get_prefixes_dirs():
            if not os.path.exists(prefix_base):
                continue
            for item in os.listdir(prefix_base):
                full_path = os.path.join(prefix_base, item)
                if os.path.isdir(full_path):
                    # If same name exists in multiple dirs, prefer the first (newest location)
                    if item not in result:
                        result[item] = full_path
        return result

    def init_ui(self):
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

    def refresh_prefixes(self):
        self.list_widget.clear()
        # Ensure the new prefixes dir exists
        if not os.path.exists(self.prefixes_dir):
            try:
                os.makedirs(self.prefixes_dir, exist_ok=True)
            except OSError:
                return

        try:
            all_prefixes = self._get_all_prefixes()
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

                widget = PrefixItemWidget(p, prefix_path)
                item.setSizeHint(widget.sizeHint())

                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, widget)

        except OSError as e:
            logger.error("Error reading prefixes: %s", e)

    def on_selection_changed(self):
        has_selection = len(self.list_widget.selectedItems()) > 0
        self.config_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)

    def open_selected_prefix_config(self):
        item = self.list_widget.currentItem()
        if not item:
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

        # Check shortcut dirs from both new and legacy locations for reading config
        scripts_dirs = settings_manager.get_shortcuts_dirs(game_name)
        config_path = None
        scripts_dir = None
        for sd in scripts_dirs:
            cp = os.path.join(sd, "config.json")
            if os.path.exists(cp):
                config_path = cp
                scripts_dir = sd
                break

        # Load existing config if available
        initial_config = {}
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    initial_config = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Error loading config for %s: %s", game_name, e)
        elif scripts_dir and os.path.exists(scripts_dir):
            # Fallback: Try to parse from a .sh file
            sh_files = glob.glob(os.path.join(scripts_dir, "*.sh"))
            if sh_files:
                logger.info("Config not found, extracting from %s", sh_files[0])
                initial_config = self.extract_config_from_sh(sh_files[0])

        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self,
            wine_prefix_path=prefix_path,
            initial_config=initial_config
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_config = dialog.get_config()

            # Save config to the primary (new) scripts dir
            scripts_dir = settings_manager.get_shortcuts_dir(game_name)
            if not os.path.exists(scripts_dir):
                os.makedirs(scripts_dir, exist_ok=True)

            config_path = os.path.join(scripts_dir, "config.json")
            try:
                with open(config_path, 'w') as f:
                    json.dump(new_config, f, indent=4)
                logger.info("Saved config to %s", config_path)
            except OSError as e:
                QMessageBox.warning(self, "Save Error", f"Failed to save config: {e}")

            # Update scripts in the primary dir
            self.update_scripts(scripts_dir, prefix_path, new_config)

    def extract_config_from_sh(self, script_path):
        """
        Parses a .sh script to extract environment variables set before umu-run.
        Returns a dict of key-value pairs.
        """
        config = {}
        try:
            with open(script_path, 'r') as f:
                content = f.read()
            
            lines = content.splitlines()
            umu_run_line = ""
            
            # Find the line with umu-run, searching backwards
            for line in reversed(lines):
                if "umu-run" in line:
                    umu_run_line = line
                    break
            
            if umu_run_line:
                # Split at umu-run to get the env var part
                env_part = umu_run_line.split("umu-run")[0]

                # Check if mangohud is used in front of umu-run
                if "mangohud" in env_part.lower():
                    config["MANGOHUD"] = "1"
                    env_part = env_part.replace("mangohud", "").strip()
                
                # Regex to find KEY="VALUE"
                matches = re.findall(r'(\w+)="(.*?)"', env_part)
                
                for key, value in matches:
                    if key not in ["WINEPREFIX"]:
                         config[key] = value
                         
        except (OSError, IOError) as e:
            logger.error("Error extracting config from %s: %s", script_path, e)
            
        return config

    def update_scripts(self, scripts_dir, prefix_path, config):
        """
        Updates all .sh scripts with the new environment variables
        from the config, preserving the executable path.
        Scans both primary and legacy script directories for .sh files.
        """
        # Extract game name from scripts_dir path (e.g. ".../shortcut_scripts/dark earth" -> "dark earth")
        game_name = os.path.basename(scripts_dir)

        # Collect .sh files from all script dirs (new + legacy)
        sh_files = []
        for sd in settings_manager.get_shortcuts_dirs(game_name):
            if os.path.exists(sd):
                sh_files.extend(glob.glob(os.path.join(sd, "*.sh")))

        if not sh_files:
            logger.info("No .sh scripts found to update.")
            return
            
        proton_path = config.get("PROTONPATH") or settings_manager.get("PROTONPATH") or "GE-Proton"

        env_part = build_umu_env_prefix(proton_path, prefix_path, config)

        count = 0
        for script_path in sh_files:
            try:
                logger.info("Checking script: %s", script_path)
                with open(script_path, 'r') as f:
                    content = f.read()
                
                lines = content.splitlines()
                umu_run_line = ""
                
                # Find the line with umu-run, searching backwards
                for line in reversed(lines):
                    if "umu-run" in line:
                        umu_run_line = line
                        break
                
                # Check if it looks like a valid wrapper script
                if umu_run_line:
                    # Extract the command after umu-run
                    parts = umu_run_line.split("umu-run")
                    if len(parts) > 1:
                        exe_args = parts[1].strip() # This is "path/to/exe"
                        
                        # Reconstruct command
                        new_command = f"{env_part}umu-run {exe_args}"
                        
                        # Reconstruct file content
                        new_content = "#!/bin/sh\n\n# Auto-generated by Gameyfin\n" + new_command + "\n"
                        
                        with open(script_path, 'w') as f:
                            f.write(new_content)
                        
                        # Ensure executable
                        os.chmod(script_path, 0o755)
                        count += 1
                        logger.info("Updated script: %s", script_path)
                    else:
                        logger.warning("Script %s has umu-run but parsing failed.", script_path)
                else:
                     logger.info("Script %s does not contain 'umu-run'.", script_path)
                        
            except (OSError, IOError) as e:
                logger.error("Failed to update script %s: %s", script_path, e)
                
        if count > 0:
            QMessageBox.information(self, "Scripts Updated", f"Updated {count} shortcut script(s) with new configuration.")
        else:
             QMessageBox.information(self, "No Scripts Updated", "No suitable .sh scripts found to update.")

    def delete_selected_prefix(self):
        item = self.list_widget.currentItem()
        if not item:
            return

        prefix_name = item.data(Qt.ItemDataRole.UserRole)
        # Use stored path (may be from legacy dir) instead of constructing from prefixes_dir
        prefix_path = item.data(Qt.ItemDataRole.UserRole + 1)
        if prefix_path is None:
            prefix_path = os.path.join(self.prefixes_dir, prefix_name)

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
            import shutil
            try:
                shutil.rmtree(prefix_path)
                self.refresh_prefixes()

                # Also try to delete the shortcut scripts folder from both locations
                game_name = prefix_name
                if game_name.endswith("_pfx"):
                    game_name = game_name[:-4]
                for scripts_dir in settings_manager.get_shortcuts_dirs(game_name):
                    if os.path.exists(scripts_dir):
                        shutil.rmtree(scripts_dir)

            except (OSError, IOError) as e:
                QMessageBox.critical(self, "Error", f"Failed to delete prefix:\n{e}")

