import os
import json
import glob
import subprocess
import configparser
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QListWidget, QPushButton, 
                             QHBoxLayout, QLabel, QMessageBox, QDialog, QComboBox, QListWidgetItem, QCheckBox)
from PyQt6.QtCore import Qt
from gameyfin_frontend.dialogs import InstallConfigDialog, SelectShortcutsDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import settings_manager
from gameyfin_frontend.utils import get_xdg_user_dir

import re

class PrefixItemWidget(QWidget):
    def __init__(self, prefix_name, prefix_path, scripts_dir, parent=None):
        super().__init__(parent)
        self.prefix_name = prefix_name
        self.prefix_path = prefix_path
        self.scripts_dir = scripts_dir
        
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
        if os.path.exists(self.scripts_dir):
            scripts = glob.glob(os.path.join(self.scripts_dir, "*.sh"))
            scripts.sort()
            
            if not scripts:
                self.script_combo.addItem("No scripts found")
                self.script_combo.setEnabled(False)
            else:
                self.script_combo.addItem("Select script to launch...")
                for s in scripts:
                    self.script_combo.addItem(os.path.basename(s), s)
        else:
            self.script_combo.addItem("No scripts directory")
            self.script_combo.setEnabled(False)

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
            except Exception as e:
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
        # Load config.json if available
        config_path = os.path.join(self.scripts_dir, "config.json")
        install_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    install_config = json.load(f)
            except Exception as e:
                print(f"Error loading config for shortcuts: {e}")

        home_dir = os.path.expanduser("~")
        os.makedirs(self.scripts_dir, exist_ok=True)

        # 1. ALWAYS create/update .sh scripts for ALL detected shortcuts
        # This ensures the selectbox always has all options.
        for original_path in all_desktop_files:
            try:
                # configparser needs a section header
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
                proton_path = settings_manager.get("PROTONPATH", "GE-Proton")
                env_prefix = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{self.prefix_path}\" "
                cmd_prefix = ""

                for key, value in install_config.items():

                    if key == "MANGOHUD" and value == "1":

                        cmd_prefix = "mangohud "

                    else:

                        env_prefix += f"{key}=\"{value}\" "

                command_to_run = f"{env_prefix} {cmd_prefix}umu-run \"{exe_path}\""
                
                script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
                script_path = os.path.join(self.scripts_dir, script_name)
                script_content = f"#!/bin/sh\n\n# Auto-generated by Gameyfin\n{command_to_run}\n"

                with open(script_path, 'w') as f:
                    f.write(script_content)
                os.chmod(script_path, 0o755)
                print(f"Created/Updated helper script: {script_path}")

            except Exception as e:
                print(f"Failed to create helper script for {original_path}: {e}")

        # 2. Manage system .desktop files
        locs = [
            (os.path.join(home_dir, get_xdg_user_dir("DESKTOP")), selected_desktop),
            (os.path.join(home_dir, ".local", "share", "applications"), selected_apps)
        ]

        for target_dir, selected_list in locs:
            os.makedirs(target_dir, exist_ok=True)
            
            # Remove those NOT selected for this specific location
            to_remove = [f for f in all_desktop_files if f not in selected_list]
            for original_path in to_remove:
                target_path = os.path.join(target_dir, os.path.basename(original_path))
                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                        print(f"Removed system shortcut: {target_path}")
                    except Exception as e:
                        print(f"Failed to remove system shortcut {target_path}: {e}")

            # Create/Update those selected for this specific location
            for original_path in selected_list:
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
                    
                    # Icon handling
                    icon_name = entry.get('Icon')
                    if icon_name:
                        icons_base_dir = os.path.join(os.path.dirname(original_path), "icons")
                        sizes_to_check = ["256x256", "128x128", "64x64", "48x48", "32x32"]
                        found_icon_path = None
                        for size in sizes_to_check:
                            p_png = os.path.join(icons_base_dir, size, "apps", f"{icon_name}.png")
                            p_as_is = os.path.join(icons_base_dir, size, "apps", icon_name)
                            if os.path.exists(p_png):
                                found_icon_path = p_png
                                break
                            elif os.path.exists(p_as_is):
                                found_icon_path = p_as_is
                                break
                        if found_icon_path:
                            config_parser.set('Desktop Entry', 'Icon', found_icon_path)

                    script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
                    script_path = os.path.join(self.scripts_dir, script_name)

                    config_parser.set('Desktop Entry', 'Exec', f'\"{script_path}\"')
                    config_parser.set('Desktop Entry', 'Type', 'Application')
                    config_parser.set('Desktop Entry', 'Categories', 'Application;Game;')

                    new_file_path = os.path.join(target_dir, os.path.basename(original_path))
                    with open(new_file_path, 'w') as f:
                        config_parser.write(f)
                    os.chmod(new_file_path, 0o755)
                    print(f"Successfully created system shortcut at: {new_file_path}")

                except Exception as e:
                    print(f"Failed to process system shortcut {original_path} for {target_dir}: {e}")




class PrefixManagerWidget(QWidget):
    def __init__(self, umu_database: UmuDatabase, parent=None):
        super().__init__(parent)
        self.umu_database = umu_database
        self.prefixes_dir = os.path.join(os.path.expanduser("~"), ".config", "gameyfin", "prefixes")
        self.shortcuts_base_dir = os.path.join(os.path.expanduser("~"), ".config", "gameyfin", "shortcut_scripts")
        
        self.init_ui()
        self.refresh_prefixes()

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
        if not os.path.exists(self.prefixes_dir):
            try:
                os.makedirs(self.prefixes_dir, exist_ok=True)
            except OSError:
                return

        try:
            items = os.listdir(self.prefixes_dir)
            prefixes = [item for item in items if os.path.isdir(os.path.join(self.prefixes_dir, item))]
            prefixes.sort()
            
            for p in prefixes:
                game_name = p
                if game_name.endswith("_pfx"):
                    game_name = game_name[:-4]
                
                prefix_path = os.path.join(self.prefixes_dir, p)
                scripts_dir = os.path.join(self.shortcuts_base_dir, game_name)
                
                item = QListWidgetItem(self.list_widget)
                item.setData(Qt.ItemDataRole.UserRole, p)
                
                widget = PrefixItemWidget(p, prefix_path, scripts_dir)
                item.setSizeHint(widget.sizeHint())
                
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, widget)
                
        except Exception as e:
            print(f"Error reading prefixes: {e}")


    def on_selection_changed(self):
        has_selection = len(self.list_widget.selectedItems()) > 0
        self.config_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)

    def open_selected_prefix_config(self):
        item = self.list_widget.currentItem()
        if not item:
            return
            
        prefix_name = item.data(Qt.ItemDataRole.UserRole)
        prefix_path = os.path.join(self.prefixes_dir, prefix_name)
        
        # Derive game name
        game_name = prefix_name
        if game_name.endswith("_pfx"):
            game_name = game_name[:-4]  # Remove _pfx
        
        # Determine shortcut scripts dir
        scripts_dir = os.path.join(self.shortcuts_base_dir, game_name)
        config_path = os.path.join(scripts_dir, "config.json")
        
        # Load existing config if available
        initial_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    initial_config = json.load(f)
            except Exception as e:
                print(f"Error loading config for {game_name}: {e}")
        elif os.path.exists(scripts_dir):
            # Fallback: Try to parse from a .sh file
            sh_files = glob.glob(os.path.join(scripts_dir, "*.sh"))
            if sh_files:
                print(f"Config not found, extracting from {sh_files[0]}")
                initial_config = self.extract_config_from_sh(sh_files[0])

        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self,
            wine_prefix_path=prefix_path,
            initial_config=initial_config
        )
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_config = dialog.get_config()
            
            # Save config
            if not os.path.exists(scripts_dir):
                os.makedirs(scripts_dir, exist_ok=True)
                
            try:
                with open(config_path, 'w') as f:
                    json.dump(new_config, f, indent=4)
                print(f"Saved config to {config_path}")
            except Exception as e:
                QMessageBox.warning(self, "Save Error", f"Failed to save config: {e}")
                
            # Update scripts
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
                
                # Regex to find KEY="VALUE"
                matches = re.findall(r'(\w+)="(.*?)"', env_part)
                
                for key, value in matches:
                    if key not in ["PROTONPATH", "WINEPREFIX"]:
                         config[key] = value
                         
        except Exception as e:
            print(f"Error extracting config from {script_path}: {e}")
            
        return config

    def update_scripts(self, scripts_dir, prefix_path, config):
        """
        Updates all .sh scripts in the directory with the new environment variables
        from the config, preserving the executable path.
        """
        if not os.path.exists(scripts_dir):
            return
            
        sh_files = glob.glob(os.path.join(scripts_dir, "*.sh"))
        if not sh_files:
            print("No .sh scripts found to update.")
            return
            
        proton_path = settings_manager.get("PROTONPATH", "GE-Proton")
        
        # Construct the environment part and command prefix
        env_part = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{prefix_path}\" "
        cmd_prefix = ""
        for key, value in config.items():
            if key == "MANGOHUD" and value == "1":
                cmd_prefix = "mangohud "
            else:
                env_part += f"{key}=\"{value}\" "
            
        count = 0
        for script_path in sh_files:
            try:
                print(f"Checking script: {script_path}")
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
                        new_command = f"{env_part}{cmd_prefix}umu-run {exe_args}"
                        
                        # Reconstruct file content
                        new_content = "#!/bin/sh\n\n# Auto-generated by Gameyfin\n" + new_command + "\n"
                        
                        with open(script_path, 'w') as f:
                            f.write(new_content)
                        
                        # Ensure executable
                        os.chmod(script_path, 0o755)
                        count += 1
                        print(f"Updated script: {script_path}")
                    else:
                        print(f"Script {script_path} has umu-run but parsing failed.")
                else:
                     print(f"Script {script_path} does not contain 'umu-run'.")
                        
            except Exception as e:
                print(f"Failed to update script {script_path}: {e}")
                
        if count > 0:
            QMessageBox.information(self, "Scripts Updated", f"Updated {count} shortcut script(s) with new configuration.")
        else:
             QMessageBox.information(self, "No Scripts Updated", "No suitable .sh scripts found to update.")

    def delete_selected_prefix(self):
        item = self.list_widget.currentItem()
        if not item:
            return
            
        prefix_name = item.data(Qt.ItemDataRole.UserRole)
        prefix_path = os.path.join(self.prefixes_dir, prefix_name)
        
        confirm = QMessageBox.question(
            self, 
            "Confirm Delete",
            f"Are you sure you want to delete the prefix '{prefix_name}'?\n\nPath: {prefix_path}\n\nThis cannot be undone!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if confirm == QMessageBox.StandardButton.Yes:
            import shutil
            try:
                shutil.rmtree(prefix_path)
                self.refresh_prefixes()
                
                # Also try to delete the shortcut scripts folder
                game_name = prefix_name
                if game_name.endswith("_pfx"):
                    game_name = game_name[:-4]
                scripts_dir = os.path.join(self.shortcuts_base_dir, game_name)
                if os.path.exists(scripts_dir):
                     shutil.rmtree(scripts_dir)

            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete prefix:\n{e}")
