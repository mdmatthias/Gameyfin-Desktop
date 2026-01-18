import os
import sys
import json
import subprocess
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, 
                             QPushButton, QLabel, QSpinBox, QMessageBox, QCheckBox, QHBoxLayout, QFileDialog, QComboBox)
from qt_material import list_themes
from .settings import settings_manager

class SettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        
        self.form_layout = QFormLayout()
        
        self.url_edit = QLineEdit()
        self.url_edit.setText(settings_manager.get("GF_URL"))
        self.form_layout.addRow("Gameyfin URL:", self.url_edit)
        
        self.sso_edit = QLineEdit()
        self.sso_edit.setText(settings_manager.get("GF_SSO_PROVIDER_HOST"))
        self.form_layout.addRow("SSO Provider Host:", self.sso_edit)
        
        self.width_spin = QSpinBox()
        self.width_spin.setRange(800, 3840)
        self.width_spin.setValue(settings_manager.get("GF_WINDOW_WIDTH"))
        self.form_layout.addRow("Window Width:", self.width_spin)
        
        self.height_spin = QSpinBox()
        self.height_spin.setRange(600, 2160)
        self.height_spin.setValue(settings_manager.get("GF_WINDOW_HEIGHT"))
        self.form_layout.addRow("Window Height:", self.height_spin)
        
        self.proton_edit = QLineEdit()
        self.proton_edit.setText(settings_manager.get("PROTONPATH"))
        
        self.umu_api_edit = QLineEdit()
        self.umu_api_edit.setText(settings_manager.get("GF_UMU_API_URL"))

        self.stores_edit = QLineEdit()
        stores = settings_manager.get("GF_UMU_DB_STORES")
        self.stores_edit.setText(json.dumps(stores))

        if sys.platform == "linux":
            self.form_layout.addRow("Proton Path:", self.proton_edit)
            self.form_layout.addRow("UMU API URL:", self.umu_api_edit)
            self.form_layout.addRow("UMU Stores (JSON):", self.stores_edit)

        self.minimized_check = QCheckBox()
        self.minimized_check.setChecked(bool(settings_manager.get("GF_START_MINIMIZED")))
        self.form_layout.addRow("Start Minimized:", self.minimized_check)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("auto")
        self.theme_combo.addItems(list_themes())
        current_theme = settings_manager.get("GF_THEME")
        if current_theme:
            self.theme_combo.setCurrentText(current_theme)
        self.form_layout.addRow("Theme:", self.theme_combo)

        self.icon_path_edit = QLineEdit()
        self.icon_path_edit.setPlaceholderText("(default)")
        self.icon_path_edit.setText(settings_manager.get("GF_ICON_PATH"))
        self.icon_browse_btn = QPushButton("Browse...")
        self.icon_browse_btn.clicked.connect(self.browse_icon)
        icon_layout = QHBoxLayout()
        icon_layout.addWidget(self.icon_path_edit)
        icon_layout.addWidget(self.icon_browse_btn)
        self.form_layout.addRow("Custom Tray Icon:", icon_layout)
        
        self.layout.addLayout(self.form_layout)
        
        self.save_button = QPushButton("Save and Apply")
        self.save_button.clicked.connect(self.save_settings)
        self.layout.addWidget(self.save_button)
        
        self.layout.addStretch()

    def browse_icon(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Icon", "", "Images (*.png *.jpg *.ico);;All Files (*)")
        if file_path:
            self.icon_path_edit.setText(file_path)

    def save_settings(self):
        try:
            stores = json.loads(self.stores_edit.text())
            if not isinstance(stores, list):
                raise ValueError("Stores must be a list")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Invalid JSON for stores: {e}")
            return

        settings_manager.set("GF_URL", self.url_edit.text())
        settings_manager.set("GF_SSO_PROVIDER_HOST", self.sso_edit.text())
        settings_manager.set("GF_WINDOW_WIDTH", self.width_spin.value())
        settings_manager.set("GF_WINDOW_HEIGHT", self.height_spin.value())
        settings_manager.set("PROTONPATH", self.proton_edit.text())
        settings_manager.set("GF_UMU_API_URL", self.umu_api_edit.text())
        settings_manager.set("GF_UMU_DB_STORES", stores)
        settings_manager.set("GF_START_MINIMIZED", 1 if self.minimized_check.isChecked() else 0)
        settings_manager.set("GF_THEME", self.theme_combo.currentText())
        settings_manager.set("GF_ICON_PATH", self.icon_path_edit.text())
        
        # Apply settings immediately
        if hasattr(self.window(), 'apply_settings'):
            self.window().apply_settings()
        
        QMessageBox.information(self, "Settings Saved", "Settings have been saved and applied.")
