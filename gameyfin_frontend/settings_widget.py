import os
import sys
import json
import subprocess
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QFormLayout, QLineEdit,
                             QPushButton, QLabel, QSlider, QSpinBox, QMessageBox, QCheckBox, QHBoxLayout, QFileDialog, QComboBox)
from PyQt6.QtCore import Qt
from qt_material import list_themes
from .settings import SettingsManager


class SettingsWidget(QWidget):
    def __init__(self, parent=None, settings: SettingsManager | None = None):
        super().__init__(parent)
        self.settings = settings
        self.layout = QVBoxLayout(self)

        self.form_layout = QFormLayout()

        self.url_edit = QLineEdit()
        self.url_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.url_edit.setText(settings.get("GF_URL") if settings else "")
        self.form_layout.addRow("Gameyfin URL:", self.url_edit)

        self.width_spin = QSpinBox()
        self.width_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.width_spin.setRange(800, 3840)
        self.width_spin.setValue(settings.get("GF_WINDOW_WIDTH") if settings else 1420)
        self.form_layout.addRow("Window Width:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.height_spin.setRange(600, 2160)
        self.height_spin.setValue(settings.get("GF_WINDOW_HEIGHT") if settings else 940)
        self.form_layout.addRow("Window Height:", self.height_spin)

        self.proton_edit = QLineEdit()
        self.proton_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.proton_edit.setText(settings.get("PROTONPATH") if settings else "")

        self.umu_api_edit = QLineEdit()
        self.umu_api_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.umu_api_edit.setText(settings.get("GF_UMU_API_URL") if settings else "")

        self.stores_edit = QLineEdit()
        stores = settings.get("GF_UMU_DB_STORES") if settings else []
        self.stores_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.stores_edit.setText(json.dumps(stores))

        if sys.platform == "linux":
            self.form_layout.addRow("Proton Path:", self.proton_edit)
            self.form_layout.addRow("UMU API URL:", self.umu_api_edit)
            self.form_layout.addRow("UMU Stores (JSON):", self.stores_edit)

        self.minimized_check = QCheckBox()
        self.minimized_check.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.minimized_check.setChecked(bool(settings.get("GF_START_MINIMIZED")) if settings else False)
        self.form_layout.addRow("Start Minimized:", self.minimized_check)

        self.theme_combo = QComboBox()
        self.theme_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.theme_combo.addItem("auto")
        self.theme_combo.addItems(list_themes())
        current_theme = settings.get("GF_THEME") if settings else "auto"
        if current_theme:
            self.theme_combo.setCurrentText(current_theme)
        self.form_layout.addRow("Theme:", self.theme_combo)

        self.log_level_combo = QComboBox()
        self.log_level_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        current_log = (settings.get("GF_LOG_LEVEL", "WARNING") if settings else "WARNING").upper()
        if current_log:
            self.log_level_combo.setCurrentText(current_log)
        self.form_layout.addRow("Log Level:", self.log_level_combo)

        self.icon_path_edit = QLineEdit()
        self.icon_path_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.icon_path_edit.setPlaceholderText("(default)")
        self.icon_path_edit.setText(settings.get("GF_ICON_PATH") if settings else "")
        self.icon_browse_btn = QPushButton("Browse...")
        self.icon_browse_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.icon_browse_btn.clicked.connect(self.browse_icon)
        icon_layout = QHBoxLayout()
        icon_layout.addWidget(self.icon_path_edit)
        icon_layout.addWidget(self.icon_browse_btn)
        self.form_layout.addRow("Custom Tray Icon:", icon_layout)

        # Extraction Settings
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.download_dir_edit.setPlaceholderText("(defaults to ~/Downloads/<game-name>)")
        self.download_dir_edit.setText(settings.get("GF_DEFAULT_DOWNLOAD_DIR") if settings else "")
        self.download_dir_btn = QPushButton("Browse...")
        self.download_dir_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.download_dir_btn.clicked.connect(lambda: self.browse_directory(self.download_dir_edit, "Select Download Directory"))
        download_dir_layout = QHBoxLayout()
        download_dir_layout.addWidget(self.download_dir_edit)
        download_dir_layout.addWidget(self.download_dir_btn)
        self.form_layout.addRow("Default Download Dir:", download_dir_layout)

        self.prompt_download_check = QCheckBox()
        self.prompt_download_check.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.prompt_download_check.setChecked(bool(settings.get("GF_PROMPT_DOWNLOAD_DIR")) if settings else False)
        self.form_layout.addRow("Prompt for Download Dir:", self.prompt_download_check)

        self.notifications_check = QCheckBox()
        self.notifications_check.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.notifications_check.setChecked(bool(settings.get("GF_DOWNLOAD_NOTIFICATIONS")) if settings else True)
        self.form_layout.addRow("Download Notifications:", self.notifications_check)

        # Bandwidth Throttling — QSlider with 0.1 MB/s steps (range 0–1000 → 0.0–100.0 MB/s)
        bandwidth_hbox = QHBoxLayout()
        self.bandwidth_slider = QSlider(Qt.Orientation.Horizontal)
        self.bandwidth_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bandwidth_slider.setRange(0, 1000)
        self.bandwidth_slider.setSingleStep(1)
        limit_bytes = settings.get("GF_BANDWIDTH_LIMIT") if settings else 0
        tenths_mbps = int(round((limit_bytes / (1024 * 1024)) * 10)) if isinstance(limit_bytes, (int, float)) and limit_bytes > 0 else 0
        self.bandwidth_slider.setValue(min(tenths_mbps, 1000))
        self.bandwidth_label = QLabel("Unlimited")
        self.bandwidth_label.setMinimumWidth(80)
        self._update_bandwidth_label(self.bandwidth_slider.value())
        self.bandwidth_slider.valueChanged.connect(self._update_bandwidth_label)
        self.bandwidth_slider.valueChanged.connect(self._on_bandwidth_changed)
        bandwidth_hbox.addWidget(self.bandwidth_slider)
        bandwidth_hbox.addWidget(self.bandwidth_label)
        self.form_layout.addRow("Download Speed Limit:", bandwidth_hbox)

        # --- Gamepad ---
        self.gamepad_enabled_check = QCheckBox()
        self.gamepad_enabled_check.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gamepad_enabled_check.setChecked(bool(settings.get("GF_GAMEPAD_ENABLED")) if settings else True)
        self.form_layout.addRow("Gamepad Support:", self.gamepad_enabled_check)

        self.gamepad_status_label = QLabel("No controller detected")
        self.gamepad_status_label.setStyleSheet("font-size: 11px; color: palette(mid);")
        self.form_layout.addRow("Controller:", self.gamepad_status_label)

        self.gamepad_hints_check = QCheckBox()
        self.gamepad_hints_check.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gamepad_hints_check.setChecked(bool(settings.get("GF_GAMEPAD_HINTS")) if settings else True)
        self.form_layout.addRow("Show Button Hints:", self.gamepad_hints_check)

        deadzone_hbox = QHBoxLayout()
        self.gamepad_deadzone_slider = QSlider(Qt.Orientation.Horizontal)
        self.gamepad_deadzone_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gamepad_deadzone_slider.setRange(5, 60)
        self.gamepad_deadzone_slider.setValue(self._int_setting("GF_GAMEPAD_DEADZONE", 25))
        self.gamepad_deadzone_label = QLabel()
        self.gamepad_deadzone_label.setMinimumWidth(50)
        self._update_deadzone_label(self.gamepad_deadzone_slider.value())
        self.gamepad_deadzone_slider.valueChanged.connect(self._update_deadzone_label)
        deadzone_hbox.addWidget(self.gamepad_deadzone_slider)
        deadzone_hbox.addWidget(self.gamepad_deadzone_label)
        self.form_layout.addRow("Stick Deadzone:", deadzone_hbox)

        self.gamepad_repeat_spin = QSpinBox()
        self.gamepad_repeat_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gamepad_repeat_spin.setRange(40, 600)
        self.gamepad_repeat_spin.setSingleStep(10)
        self.gamepad_repeat_spin.setSuffix(" ms")
        self.gamepad_repeat_spin.setValue(self._int_setting("GF_GAMEPAD_REPEAT_MS", 140))
        self.form_layout.addRow("Navigation Repeat:", self.gamepad_repeat_spin)

        self.gamepad_scroll_spin = QSpinBox()
        self.gamepad_scroll_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gamepad_scroll_spin.setRange(5, 400)
        self.gamepad_scroll_spin.setSingleStep(5)
        self.gamepad_scroll_spin.setSuffix(" px")
        self.gamepad_scroll_spin.setValue(self._int_setting("GF_GAMEPAD_SCROLL_SPEED", 60))
        self.form_layout.addRow("Scroll Speed:", self.gamepad_scroll_spin)

        self.layout.addLayout(self.form_layout)

        self.save_button = QPushButton("Save and Apply")
        self.save_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.save_button.clicked.connect(self.save_settings)
        self.layout.addWidget(self.save_button)

        self.layout.addStretch()

        # Wire explicit tab order: bandwidth → gamepad section → save button
        self._tab_order_chain: list[tuple[QWidget, QWidget]] = []
        chain = [
            self.bandwidth_slider,
            self.gamepad_enabled_check,
            self.gamepad_hints_check,
            self.gamepad_deadzone_slider,
            self.gamepad_repeat_spin,
            self.gamepad_scroll_spin,
            self.save_button,
        ]
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)
            self._tab_order_chain.append((first, second))

    def _int_setting(self, key: str, default: int) -> int:
        """Read a numeric setting, falling back to *default* on junk values."""
        if not self.settings:
            return default
        try:
            return int(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default

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
            # Convert tenths of MB/s → bytes/sec for internal storage; 0 means unlimited
            tenths = self.bandwidth_slider.value()
            self.settings.set("GF_BANDWIDTH_LIMIT", int(tenths / 10 * 1024 * 1024) if tenths > 0 else 0)
            self.settings.set("GF_LOG_LEVEL", self.log_level_combo.currentText().upper())
            self.settings.set("GF_GAMEPAD_ENABLED", 1 if self.gamepad_enabled_check.isChecked() else 0)
            self.settings.set("GF_GAMEPAD_HINTS", 1 if self.gamepad_hints_check.isChecked() else 0)
            self.settings.set("GF_GAMEPAD_DEADZONE", self.gamepad_deadzone_slider.value())
            self.settings.set("GF_GAMEPAD_REPEAT_MS", self.gamepad_repeat_spin.value())
            self.settings.set("GF_GAMEPAD_SCROLL_SPEED", self.gamepad_scroll_spin.value())
            self.settings.set("GF_GAMEPAD_MOUSE_SPEED", self.gamepad_mouse_spin.value())

        # Apply settings immediately
        if hasattr(self.window(), 'apply_settings'):
            self.window().apply_settings()

        QMessageBox.information(self, "Settings Saved", "Settings have been saved and applied.")

    def _update_bandwidth_label(self, value: int) -> None:
        """Update the bandwidth label to show human-readable speed."""
        if value == 0:
            self.bandwidth_label.setText("Unlimited")
        else:
            mbps = value / 10.0
            self.bandwidth_label.setText(f"{mbps:.1f} MB/s")

    def _update_deadzone_label(self, value: int) -> None:
        """Show the stick deadzone as a percentage."""
        self.gamepad_deadzone_label.setText(f"{value}%")

    def set_gamepad_status(self, text: str) -> None:
        """Display the currently detected controller (set by the main window)."""
        self.gamepad_status_label.setText(text)

    def _on_bandwidth_changed(self, value: int) -> None:
        """Optional hook — no-op, available for future wiring (e.g. live preview)."""
        pass
