"""Proton tab: download, list and remove Proton builds (Linux only)."""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

from gameyfin_frontend.services import proton_manager
from gameyfin_frontend.utils import format_size, muted_text_color
from gameyfin_frontend.workers import ProtonInstallWorker, ProtonReleasesWorker

logger = logging.getLogger(__name__)


class ProtonManagerWidget(QWidget):
    """Lists installed Proton builds and installs new ones from GitHub releases.

    Release lists are fetched the first time the tab is shown and then cached
    per source, to stay well inside GitHub's anonymous rate limit. The installed
    list is re-scanned every time the tab is shown, since umu and Steam tools
    may have added builds in the meantime.
    """

    # Emitted after a build was installed or removed
    installed_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._releases: dict[str, list[proton_manager.ProtonRelease]] = {}
        self._installed: list[proton_manager.InstalledProton] = []
        self._fetch_worker: ProtonReleasesWorker | None = None
        self._fetching_source: str | None = None
        self._install_worker: ProtonInstallWorker | None = None
        # Workers are dropped from here only once their thread has finished
        self._workers: list = []
        self._shown_once = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._build_installed_section(), 1)
        layout.addWidget(self._build_download_section())

        self.location_label = QLabel(
            f"Versions are installed to {proton_manager.install_dir()} and are "
            "also available in Steam. Pick one per game in its install/prefix "
            "configuration, or the default in Settings \u2192 UMU."
        )
        self.location_label.setWordWrap(True)
        layout.addWidget(self.location_label)
        self.refresh_theme_colors()

        self.refresh_installed()

    def _build_installed_section(self) -> QGroupBox:
        box = QGroupBox("Installed Versions")
        layout = QVBoxLayout(box)

        self.installed_list = QListWidget()
        self.installed_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.installed_list.currentItemChanged.connect(self._update_buttons)
        layout.addWidget(self.installed_list, 1)

        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.remove_button.clicked.connect(self._remove_selected)
        buttons = QHBoxLayout()
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    def _build_download_section(self) -> QGroupBox:
        box = QGroupBox("Download")
        form = QFormLayout(box)

        self.source_combo = QComboBox()
        self.source_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for source in proton_manager.SOURCES:
            self.source_combo.addItem(source.name, source)
            self.source_combo.setItemData(self.source_combo.count() - 1, source.description,
                                          Qt.ItemDataRole.ToolTipRole)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        form.addRow("Source:", self.source_combo)

        self.version_combo = QComboBox()
        self.version_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.version_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.version_combo.setMinimumContentsLength(20)
        self.version_combo.currentIndexChanged.connect(self._update_buttons)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_button.setToolTip("Reload the list of releases from GitHub")
        self.refresh_button.clicked.connect(lambda: self._fetch_releases(force=True))
        self.install_button = QPushButton("Install")
        self.install_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.install_button.clicked.connect(self._install_selected)
        version_row = QHBoxLayout()
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(self.refresh_button)
        version_row.addWidget(self.install_button)
        form.addRow("Version:", version_row)

        # Progress and status rows are hidden while unused, so they leave no gap
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_button.clicked.connect(self._cancel_install)
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.addWidget(self.progress_bar, 1)
        progress_layout.addWidget(self.cancel_button)
        form.addRow(self.progress_row)
        form.setRowVisible(self.progress_row, False)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        form.addRow(self.status_label)
        form.setRowVisible(self.status_label, False)
        self._form = form
        return box

    # ------------------------------------------------------------------
    # Installed builds
    # ------------------------------------------------------------------

    def refresh_installed(self) -> None:
        """Re-scan the compatibility tools directories."""
        selected = self.installed_list.currentItem()
        selected_path = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self._installed = proton_manager.list_installed()
        self.installed_list.clear()
        for build in self._installed:
            item = QListWidgetItem(build.name)
            item.setData(Qt.ItemDataRole.UserRole, build.path)
            item.setToolTip(build.path)
            self.installed_list.addItem(item)
            if build.path == selected_path:
                self.installed_list.setCurrentItem(item)
        if not self._installed:
            placeholder = QListWidgetItem("No Proton versions installed yet")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.installed_list.addItem(placeholder)
        self._populate_versions()
        self._update_buttons()

    def _selected_installed_path(self) -> str | None:
        item = self.installed_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _remove_selected(self) -> None:
        path = self._selected_installed_path()
        if not path:
            return
        name = proton_manager.display_name(path)
        answer = QMessageBox.question(
            self, "Remove Proton Version",
            f"Remove {name}?\n\n{path}\n\nGames configured to use it will not "
            "start until you pick another version. Steam loses it too.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            proton_manager.remove(path)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Remove Failed", f"Could not remove {name}:\n{e}")
            return
        self._set_status(f"Removed {name}.")
        self.refresh_installed()
        self.installed_changed.emit()

    # ------------------------------------------------------------------
    # Available releases
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if self._install_worker is None:
            self.refresh_installed()
        if not self._shown_once:
            self._shown_once = True
            self._fetch_releases()

    def _current_source(self) -> proton_manager.ProtonSource:
        return self.source_combo.currentData()

    def _on_source_changed(self, _index: int) -> None:
        self._populate_versions()
        if self._current_source().name not in self._releases:
            self._fetch_releases()

    def _fetch_releases(self, force: bool = False) -> None:
        source = self._current_source()
        if not force and source.name in self._releases:
            self._populate_versions()
            return
        if self._fetch_worker is not None:
            return
        self._fetching_source = source.name
        self._set_status(f"Loading {source.name} releases…")
        self.refresh_button.setEnabled(False)
        worker = ProtonReleasesWorker(source)
        worker.result_ready.connect(self._on_releases_fetched)
        self._track(worker)
        self._fetch_worker = worker
        worker.start()

    def _on_releases_fetched(self, releases, error: str) -> None:  # noqa: ANN001
        source_name = self._fetching_source
        self._fetch_worker = None
        self._fetching_source = None
        self.refresh_button.setEnabled(True)
        if error:
            self._set_status(f"Could not load {source_name} releases: {error}")
        else:
            self._releases[source_name] = releases or []
            if not self._install_worker:
                self._set_status("")
        self._populate_versions()
        # The user may have switched sources while this request was running
        if self._current_source().name not in self._releases and not error:
            self._fetch_releases()

    def _populate_versions(self) -> None:
        releases = self._releases.get(self._current_source().name)
        previous = self.version_combo.currentData()
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        if releases is None:
            self.version_combo.addItem("(not loaded)", None)
        elif not releases:
            self.version_combo.addItem("(no releases found)", None)
        else:
            for release in releases:
                label = release.name
                details = [d for d in (release.published,
                                       format_size(release.size) if release.size else "") if d]
                if details:
                    label += f"  ({', '.join(details)})"
                if release.prerelease:
                    label += "  [pre-release]"
                if proton_manager.is_installed(release, self._installed):
                    label += "  — installed"
                self.version_combo.addItem(label, release)
                if previous is not None and release.url == previous.url:
                    self.version_combo.setCurrentIndex(self.version_combo.count() - 1)
        self.version_combo.blockSignals(False)
        self._update_buttons()

    # ------------------------------------------------------------------
    # Installing
    # ------------------------------------------------------------------

    def _install_selected(self) -> None:
        release = self.version_combo.currentData()
        if release is None or self._install_worker is not None:
            return
        if proton_manager.is_installed(release, self._installed):
            answer = QMessageBox.question(
                self, "Reinstall Proton Version",
                f"{release.name} is already installed. Download and install it again?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        worker = ProtonInstallWorker(release)
        worker.progress.connect(self._on_install_progress)
        worker.result_ready.connect(self._on_install_finished)
        self._track(worker)
        self._install_worker = worker
        self.progress_bar.setValue(0)
        self._form.setRowVisible(self.progress_row, True)
        self.cancel_button.setEnabled(True)
        self._set_status(f"Downloading {release.name}…")
        self._update_buttons()
        worker.start()

    def _on_install_progress(self, stage: str, done: int, total: int) -> None:
        if self._install_worker is None:
            return
        name = self._install_worker.release.name
        percent = int(done * 100 / total) if total else 0
        if stage == "download":
            self.progress_bar.setValue(min(percent, 100))
            sizes = format_size(done) + (f" / {format_size(total)}" if total else "")
            self._set_status(f"Downloading {name}… {sizes}")
        else:
            self.progress_bar.setValue(min(percent, 100))
            self._set_status(f"Extracting {name}…")

    def _on_install_finished(self, path: str, error: str) -> None:
        release = self._install_worker.release if self._install_worker else None
        self._install_worker = None
        self._form.setRowVisible(self.progress_row, False)
        if error:
            self._set_status(error if not release else f"{release.name}: {error}")
        else:
            self._set_status(f"Installed {proton_manager.display_name(path)}.")
        self.refresh_installed()
        if not error:
            self.installed_changed.emit()

    def _cancel_install(self) -> None:
        if self._install_worker is not None:
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelling…")
            self._install_worker.stop()

    def is_busy(self) -> bool:
        """Return True while an install is running."""
        return self._install_worker is not None

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """Cancel an install and wait briefly for worker threads to finish."""
        if self._install_worker is not None:
            self._install_worker.stop()
        for worker in list(self._workers):
            worker.wait(timeout_ms)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self._form.setRowVisible(self.status_label, bool(text))

    def _track(self, worker) -> None:  # noqa: ANN001
        """Keep *worker* referenced until its thread has finished."""
        self._workers.append(worker)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)

    def _update_buttons(self, *_args) -> None:
        busy = self._install_worker is not None
        self.install_button.setEnabled(not busy and self.version_combo.currentData() is not None)
        self.remove_button.setEnabled(not busy and bool(self._selected_installed_path()))
        self.source_combo.setEnabled(not busy)

    def refresh_theme_colors(self) -> None:
        """Re-apply palette-derived colours after the theme changed."""
        self.location_label.setStyleSheet(f"font-size: 11px; color: {muted_text_color(self)};")
