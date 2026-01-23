import os
import json
import glob
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QListWidget, QPushButton, 
                             QHBoxLayout, QLabel, QMessageBox, QDialog)
from PyQt6.QtCore import Qt
from gameyfin_frontend.dialogs import InstallConfigDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import settings_manager

import re

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
        header_label = QLabel("Installed Prefixes")
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
                self.list_widget.addItem(p)
                
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
            
        prefix_name = item.text()
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
            last_line = lines[-1] if lines else ""
            
            if "umu-run" in last_line:
                # Split at umu-run to get the env var part
                env_part = last_line.split("umu-run")[0]
                
                # Regex to find KEY="VALUE"
                # This handles simple cases. If values contain escaped quotes, it might be tricky,
                # but standard Gameyfin generation uses simple quotes.
                matches = re.findall(r'(\w+)="(.*?)"', env_part)
                
                for key, value in matches:
                    # Ignore standard paths if you don't want them in the user config
                    # But InstallConfigDialog might want them to show current state?
                    # Usually PROTONPATH and WINEPREFIX are managed by the app, but
                    # InstallConfigDialog doesn't show them in the editable fields except via logic.
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
        
        # Construct the environment part of the command
        env_part = f"PROTONPATH=\"{proton_path}\" WINEPREFIX=\"{prefix_path}\" "
        for key, value in config.items():
            env_part += f"{key}=\"{value}\" "
            
        count = 0
        for script_path in sh_files:
            try:
                with open(script_path, 'r') as f:
                    content = f.read()
                
                lines = content.splitlines()
                last_line = lines[-1] if lines else ""
                
                # Check if it looks like a valid wrapper script
                if "umu-run" in last_line:
                    # Extract the command after umu-run
                    parts = last_line.split("umu-run")
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
            
        prefix_name = item.text()
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