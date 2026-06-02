import os
import sys
import json
import subprocess
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, 
                             QPushButton, QLabel, QSpinBox, QMessageBox, QCheckBox, QHBoxLayout, QFileDialog, QComboBox)
from qt_material import list_themes
from .settings import SettingsManager

class SettingsWidget(QWidget):
    def __init__(self, parent=None, settings: SettingsManager | None = None):
        super().__init__(parent)
        self.settings = settings
        self.layout = QVBoxLayout(self)

        self.form_layout = QFormLayout()

        self.url_edit = QLineEdit()
        self.url_edit.setText(settings.get("GF_URL") if settings else "")
        self.form_layout.addRow("Gameyfin URL:", self.url_edit)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(800, 3840)
        self.width_spin.setValue(settings.get("GF_WINDOW_WIDTH") if settings else 1420)
        self.form_layout.addRow("Window Width:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(600, 2160)
        self.height_spin.setValue(settings.get("GF_WINDOW_HEIGHT") if settings else 940)
        self.form_layout.addRow("Window Height:", self.height_spin)

        self.proton_edit = QLineEdit()
        self.proton_edit.setText(settings.get("PROTONPATH") if settings else "")

        self.umu_api_edit = QLineEdit()
        self.umu_api_edit.setText(settings.get("GF_UMU_API_URL") if settings else "")

        self.stores_edit = QLineEdit()
        stores = settings.get("GF_UMU_DB_STORES") if settings else []
        self.stores_edit.setText(json.dumps(stores))

        if sys.platform == "linux":
            self.form_layout.addRow("Proton Path:", self.proton_edit)
            self.form_layout.addRow("UMU API URL:", self.umu_api_edit)
            self.form_layout.addRow("UMU Stores (JSON):", self.stores_edit)

        self.minimized_check = QCheckBox()
        self.minimized_check.setChecked(bool(settings.get("GF_START_MINIMIZED")) if settings else False)
        self.form_layout.addRow("Start Minimized:", self.minimized_check)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("auto")
        self.theme_combo.addItems(list_themes())
        current_theme = settings.get("GF_THEME") if settings else "auto"
        if current_theme:
            self.theme_combo.setCurrentText(current_theme)
        self.form_layout.addRow("Theme:", self.theme_combo)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        current_log = (settings.get("GF_LOG_LEVEL", "WARNING") if settings else "WARNING").upper()
        if current_log:
            self.log_level_combo.setCurrentText(current_log)
        self.form_layout.addRow("Log Level:", self.log_level_combo)

        self.icon_path_edit = QLineEdit()
        self.icon_path_edit.setPlaceholderText("(default)")
        self.icon_path_edit.setText(settings.get("GF_ICON_PATH") if settings else "")
        self.icon_browse_btn = QPushButton("Browse...")
        self.icon_browse_btn.clicked.connect(self.browse_icon)
        icon_layout = QHBoxLayout()
        icon_layout.addWidget(self.icon_path_edit)
        icon_layout.addWidget(self.icon_browse_btn)
        self.form_layout.addRow("Custom Tray Icon:", icon_layout)

        # Extraction Settings
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setPlaceholderText("(defaults to ~/Downloads/<game-name>)")
        self.download_dir_edit.setText(settings.get("GF_DEFAULT_DOWNLOAD_DIR") if settings else "")
        self.download_dir_btn = QPushButton("Browse...")
        self.download_dir_btn.clicked.connect(lambda: self.browse_directory(self.download_dir_edit, "Select Download Directory"))
        download_dir_layout = QHBoxLayout()
        download_dir_layout.addWidget(self.download_dir_edit)
        download_dir_layout.addWidget(self.download_dir_btn)
        self.form_layout.addRow("Default Download Dir:", download_dir_layout)

        self.prompt_download_check = QCheckBox()
        self.prompt_download_check.setChecked(bool(settings.get("GF_PROMPT_DOWNLOAD_DIR")) if settings else False)
        self.form_layout.addRow("Prompt for Download Dir:", self.prompt_download_check)

        self.notifications_check = QCheckBox()
        self.notifications_check.setChecked(bool(settings.get("GF_DOWNLOAD_NOTIFICATIONS")) if settings else True)
        self.form_layout.addRow("Download Notifications:", self.notifications_check)

        self.layout.addLayout(self.form_layout)
        
        self.save_button = QPushButton("Save and Apply")
        self.save_button.clicked.connect(self.save_settings)
        self.layout.addWidget(self.save_button)
        
        self.layout.addStretch()

    def browse_icon(self):
        """Open a file dialog to select a custom tray icon and write the path to the edit field."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Icon", "", "Images (*.png *.jpg *.ico);;All Files (*)")
        if file_path:
            self.icon_path_edit.setText(file_path)

    def browse_directory(self, line_edit, title):
        """Open a directory selection dialog and write the selected path to the given QLineEdit."""
        dir_path = QFileDialog.getExistingDirectory(self, title, line_edit.text())
        if dir_path:
            line_edit.setText(dir_path)

    def save_settings(self):
        """Validate and persist all settings, then apply them immediately."""
        try:
            stores = json.loads(self.stores_edit.text())
            if not isinstance(stores, list):
                raise ValueError("Stores must be a list")
        except (json.JSONDecodeError, ValueError) as e:
            QMessageBox.critical(self, "Error", f"Invalid JSON for stores: {e}")
            return

        if self.settings:
            self.settings.set("GF_URL", self.url_edit.text())
            self.settings.set("GF_WINDOW_WIDTH", self.width_spin.value())
            self.settings.set("GF_WINDOW_HEIGHT", self.height_spin.value())
            self.settings.set("PROTONPATH", self.proton_edit.text())
            self.settings.set("GF_UMU_API_URL", self.umu_api_edit.text())
            self.settings.set("GF_UMU_DB_STORES", stores)
            self.settings.set("GF_START_MINIMIZED", 1 if self.minimized_check.isChecked() else 0)
            self.settings.set("GF_THEME", self.theme_combo.currentText())
            self.settings.set("GF_ICON_PATH", self.icon_path_edit.text())
            self.settings.set("GF_DEFAULT_DOWNLOAD_DIR", self.download_dir_edit.text())
            self.settings.set("GF_PROMPT_DOWNLOAD_DIR", 1 if self.prompt_download_check.isChecked() else 0)
            self.settings.set("GF_DOWNLOAD_NOTIFICATIONS", 1 if self.notifications_check.isChecked() else 0)
            self.settings.set("GF_LOG_LEVEL", self.log_level_combo.currentText().upper())
        
        # Apply settings immediately
        if hasattr(self.window(), 'apply_settings'):
            self.window().apply_settings()
        
        QMessageBox.information(self, "Settings Saved", "Settings have been saved and applied.")
