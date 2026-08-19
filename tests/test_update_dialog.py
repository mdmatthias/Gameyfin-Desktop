"""Tests for the UpdateDialog state machine (check → download → install)."""

import os
from unittest.mock import patch

import pytest

from gameyfin_frontend.config import APP_VERSION


class FakeSignal:
    """Minimal stand-in for a pyqtSignal that emits synchronously."""

    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self, *args):
        for handler in list(self._handlers):
            handler(*args)


def make_check_worker_class(release=None, error="", emit_on_start=True):
    """Build a fake UpdateCheckWorker class with the given outcome."""

    class FakeCheckWorker:
        def __init__(self):
            self.finished = FakeSignal()
            self._release = release
            self._error = error

        def start(self):
            if emit_on_start:
                self.finished.emit(self._release, self._error)

        def wait(self, timeout=0):
            return True

    return FakeCheckWorker


def make_release(tag="v2.9.4"):
    return {
        "tag_name": tag,
        "assets": [
            {
                "name": f"Gameyfin-Desktop-{tag}.flatpak",
                "browser_download_url": f"https://example.com/{tag}.flatpak",
            },
            {
                "name": f"Gameyfin-Desktop-{tag}.exe",
                "browser_download_url": f"https://example.com/{tag}.exe",
            },
        ],
    }


class TestUpdateDialogCheckPhase:
    def test_initial_state_is_checking(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(emit_on_start=False),
        ):
            dialog = UpdateDialog()
            qtbot.addWidget(dialog)
        assert dialog._state == "checking"
        assert dialog.ok_button.isEnabled() is False
        assert "Checking for updates" in dialog.status_label.text()

    def test_up_to_date(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v2.0.0")),
        ):
            dialog = UpdateDialog()
            qtbot.addWidget(dialog)
        assert dialog._state == "up_to_date"
        assert "up to date" in dialog.status_label.text()
        # Versions are shown without a leading "v"
        assert f"Current version: {APP_VERSION}" in dialog.status_label.text()
        assert "Latest release: 2.0.0" in dialog.status_label.text()
        assert dialog.ok_button.isEnabled()
        assert dialog.cancel_button.isHidden()

    def test_update_available(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True):
            dialog = UpdateDialog()
            qtbot.addWidget(dialog)
        assert dialog._state == "update_available"
        assert "Update available: 9.9.9 (current: " in dialog.status_label.text()
        assert dialog.ok_button.text() == "Update"
        assert dialog.ok_button.isEnabled()
        assert not dialog.cancel_button.isHidden()

    def test_check_error(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(error="connection refused"),
        ):
            dialog = UpdateDialog()
            qtbot.addWidget(dialog)
        assert dialog._state == "error"
        assert "connection refused" in dialog.status_label.text()

    def test_missing_asset_shows_error(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        release = {"tag_name": "v9.9.9", "assets": []}
        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=release),
        ):
            dialog = UpdateDialog()
            qtbot.addWidget(dialog)
        assert dialog._state == "error"
        assert "no download suitable" in dialog.status_label.text()


class TestUpdateDialogPrefetchedRelease:
    def test_prefetched_release_skips_check(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        release = make_release("v9.9.9")
        with patch("gameyfin_frontend.dialogs.UpdateCheckWorker") as mock_worker_cls, \
                patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True):
            dialog = UpdateDialog(release=release)
            qtbot.addWidget(dialog)
        mock_worker_cls.assert_not_called()
        assert dialog._state == "update_available"
        assert "Update available: 9.9.9 (current: " in dialog.status_label.text()

    def test_prefetched_release_up_to_date(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        release = make_release("v2.0.0")
        with patch("gameyfin_frontend.dialogs.UpdateCheckWorker") as mock_worker_cls:
            dialog = UpdateDialog(release=release)
            qtbot.addWidget(dialog)
        mock_worker_cls.assert_not_called()
        assert dialog._state == "up_to_date"

    def test_prefetched_release_missing_asset_shows_error(self, qtbot):
        from gameyfin_frontend.dialogs import UpdateDialog

        release = {"tag_name": "v9.9.9", "assets": []}
        with patch("gameyfin_frontend.dialogs.UpdateCheckWorker") as mock_worker_cls:
            dialog = UpdateDialog(release=release)
            qtbot.addWidget(dialog)
        mock_worker_cls.assert_not_called()
        assert dialog._state == "error"


class TestUpdateDialogDownloadFlow:
    def _fake_download_worker(self, tmp_path, emit_finished=True, emit_error=None):
        """Build a fake UpdateDownloadWorker that records calls and emits outcomes."""
        instances = []

        class FakeDownloadWorker:
            def __init__(self, url, target_path, bandwidth_limit=0):
                self.url = url
                self.target_path = target_path
                self.bandwidth_limit = bandwidth_limit
                self.progress = FakeSignal()
                self.bytes_received = FakeSignal()
                self.finished = FakeSignal()
                self.error = FakeSignal()
                self.stopped = False
                instances.append(self)

            def start(self):
                if emit_error is not None:
                    self.error.emit(emit_error)
                elif emit_finished:
                    with open(self.target_path, "wb") as f:
                        f.write(b"bundle")
                    self.finished.emit(self.target_path)

            def stop(self):
                self.stopped = True

            def wait(self, timeout=0):
                return True

        return FakeDownloadWorker, instances

    def test_linux_download_and_install_success(self, qtbot, fresh_settings, tmp_path):
        from gameyfin_frontend.dialogs import UpdateDialog

        fake_download, downloads = self._fake_download_worker(tmp_path)
        install_instances = []

        class FakeInstallWorker:
            def __init__(self, flatpak_path):
                self.flatpak_path = flatpak_path
                self.finished = FakeSignal()
                install_instances.append(self)

            def start(self):
                self.finished.emit(True, "Installed")

            def wait(self, timeout=0):
                return True

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.UpdateDownloadWorker", fake_download), \
             patch("gameyfin_frontend.dialogs.FlatpakInstallWorker", FakeInstallWorker), \
             patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True), \
             patch("gameyfin_frontend.dialogs.is_running_in_flatpak", return_value=True):
            dialog = UpdateDialog(settings=fresh_settings)
            qtbot.addWidget(dialog)
            assert dialog._state == "update_available"

            dialog.ok_button.click()

        assert dialog._state == "done"
        assert "installed successfully" in dialog.status_label.text()
        assert len(downloads) == 1
        assert downloads[0].url == "https://example.com/v9.9.9.flatpak"
        assert downloads[0].target_path.startswith(fresh_settings.get_config_dir())
        assert len(install_instances) == 1
        assert install_instances[0].flatpak_path == downloads[0].target_path
        # Bundle is removed after a successful install
        assert not os.path.exists(downloads[0].target_path)

    def test_linux_non_flatpak_shows_no_auto_update(self, qtbot):
        """Non-Flatpak Linux should not offer a download — shows releases link."""
        from gameyfin_frontend.dialogs import UpdateDialog

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.can_auto_update", return_value=False):
            dialog = UpdateDialog()
            qtbot.addWidget(dialog)
            dialog.show()

        assert dialog._state == "update_available_external"
        assert "Update 9.9.9 is available" in dialog.status_label.text()
        assert dialog.releases_button.isVisible()
        assert "View release page on GitHub" in dialog.releases_button.text()
        assert dialog.ok_button.isEnabled()
        assert dialog.cancel_button.isHidden()

    def test_install_failure_keeps_file_and_shows_error(self, qtbot, fresh_settings, tmp_path):
        from gameyfin_frontend.dialogs import UpdateDialog

        fake_download, downloads = self._fake_download_worker(tmp_path)

        class FakeInstallWorker:
            def __init__(self, flatpak_path):
                self.flatpak_path = flatpak_path
                self.finished = FakeSignal()

            def start(self):
                self.finished.emit(False, "flatpak not found")

            def wait(self, timeout=0):
                return True

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.UpdateDownloadWorker", fake_download), \
             patch("gameyfin_frontend.dialogs.FlatpakInstallWorker", FakeInstallWorker), \
             patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True), \
             patch("gameyfin_frontend.dialogs.is_running_in_flatpak", return_value=True):
            dialog = UpdateDialog(settings=fresh_settings)
            qtbot.addWidget(dialog)
            dialog.ok_button.click()

        assert dialog._state == "error"
        assert "flatpak not found" in dialog.status_label.text()
        # Bundle is kept so the user can install it manually
        assert os.path.exists(downloads[0].target_path)

    def test_download_error(self, qtbot, fresh_settings, tmp_path):
        from gameyfin_frontend.dialogs import UpdateDialog

        fake_download, _ = self._fake_download_worker(tmp_path, emit_error="Network error: 500")

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.UpdateDownloadWorker", fake_download), \
             patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True):
            dialog = UpdateDialog(settings=fresh_settings)
            qtbot.addWidget(dialog)
            dialog.ok_button.click()

        assert dialog._state == "error"
        assert "Network error: 500" in dialog.status_label.text()

    def test_cancel_during_download_stops_worker(self, qtbot, fresh_settings, tmp_path):
        from gameyfin_frontend.dialogs import UpdateDialog

        fake_download, downloads = self._fake_download_worker(tmp_path, emit_finished=False)

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.UpdateDownloadWorker", fake_download), \
             patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True):
            dialog = UpdateDialog(settings=fresh_settings)
            qtbot.addWidget(dialog)
            dialog.ok_button.click()
            assert dialog._state == "downloading"
            dialog.cancel_button.click()

        assert downloads[0].stopped is True

    def test_progress_updates_widgets(self, qtbot, fresh_settings, tmp_path):
        from gameyfin_frontend.dialogs import UpdateDialog

        fake_download, downloads = self._fake_download_worker(tmp_path, emit_finished=False)

        with patch(
            "gameyfin_frontend.dialogs.UpdateCheckWorker",
            make_check_worker_class(release=make_release("v9.9.9")),
        ), patch("gameyfin_frontend.dialogs.UpdateDownloadWorker", fake_download), \
             patch("gameyfin_frontend.dialogs.can_auto_update", return_value=True):
            dialog = UpdateDialog(settings=fresh_settings)
            qtbot.addWidget(dialog)
            dialog.ok_button.click()

            dialog._on_download_progress(42)
            dialog._on_download_bytes(50, 100)

        assert dialog.progress_bar.value() == 42
        assert "50" in dialog.detail_label.text()
        assert not dialog.progress_bar.isHidden()
