"""Tests for 32-bit GL driver detection/installation and its dialog."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from gameyfin_frontend.config import FDO_RUNTIME_VERSION
from gameyfin_frontend.services import gl32_service
from gameyfin_frontend.services.gl32_service import (
    gl_drivers_in,
    install_gl32_drivers,
    missing_gl32_drivers,
)


def make_gl_dir(root, drivers, empty=()):
    """Lay out a GL dir like the sandbox's: driver dirs plus merge-dirs."""
    for name in ("glvnd/egl_vendor.d", "vulkan/icd.d", "lib/dri"):
        (root / name).mkdir(parents=True)
    for driver in drivers:
        (root / driver / "lib").mkdir(parents=True)
        (root / driver / "lib" / "libGLX_mesa.so.0").touch()
    for driver in empty:
        (root / driver / "share").mkdir(parents=True)
    return str(root)


@pytest.fixture()
def in_flatpak(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "org.gameyfin.Gameyfin-Desktop")


class TestGlDriversIn:
    def test_finds_driver_dirs_only(self, tmp_path):
        gl_dir = make_gl_dir(tmp_path, ["default", "nvidia-580-82-07"])
        assert gl_drivers_in(gl_dir) == {"default", "nvidia-580-82-07"}

    def test_ignores_driver_dir_without_libs(self, tmp_path):
        gl_dir = make_gl_dir(tmp_path, [], empty=["default"])
        assert gl_drivers_in(gl_dir) == set()

    def test_missing_dir(self, tmp_path):
        assert gl_drivers_in(str(tmp_path / "nope")) == set()


class TestMissingGl32Drivers:
    def test_reports_missing_driver(self, tmp_path, in_flatpak):
        gl64 = make_gl_dir(tmp_path / "64", ["default"])
        gl32 = make_gl_dir(tmp_path / "32", [], empty=["default"])
        assert missing_gl32_drivers(gl64, gl32) == ["default"]

    def test_nothing_missing(self, tmp_path, in_flatpak):
        gl64 = make_gl_dir(tmp_path / "64", ["default"])
        gl32 = make_gl_dir(tmp_path / "32", ["default"])
        assert missing_gl32_drivers(gl64, gl32) == []

    def test_host_driver_ignored(self, tmp_path, in_flatpak):
        gl64 = make_gl_dir(tmp_path / "64", ["host"])
        gl32 = make_gl_dir(tmp_path / "32", [])
        assert missing_gl32_drivers(gl64, gl32) == []

    def test_empty_outside_flatpak(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        gl64 = make_gl_dir(tmp_path / "64", ["default"])
        gl32 = make_gl_dir(tmp_path / "32", [])
        assert missing_gl32_drivers(gl64, gl32) == []


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class TestInstallGl32Drivers:
    def test_adds_remote_then_installs_on_host(self):
        with patch.object(gl32_service.subprocess, "run", return_value=completed()) as run:
            ok, _ = install_gl32_drivers(["default"])
        assert ok
        commands = [c.args[0] for c in run.call_args_list]
        assert commands[0][:5] == ["flatpak-spawn", "--host", "flatpak", "remote-add", "--user"]
        assert commands[1][:4] == ["flatpak-spawn", "--host", "flatpak", "install"]
        assert commands[1][-1] == f"org.freedesktop.Platform.GL32.default//{FDO_RUNTIME_VERSION}"

    def test_falls_back_to_legacy_branch(self):
        results = [completed(), completed(1, stderr="not found"), completed()]
        with patch.object(gl32_service.subprocess, "run", side_effect=results) as run:
            ok, _ = install_gl32_drivers(["nvidia-580-82-07"])
        assert ok
        assert run.call_args_list[2].args[0][-1] == "org.freedesktop.Platform.GL32.nvidia-580-82-07//1.4"

    def test_fails_when_no_branch_installs(self):
        results = [completed(), completed(1, stderr="a"), completed(1, stderr="b")]
        with patch.object(gl32_service.subprocess, "run", side_effect=results):
            ok, output = install_gl32_drivers(["default"])
        assert not ok
        assert "b" in output

    def test_fails_when_remote_add_fails(self):
        with patch.object(gl32_service.subprocess, "run", return_value=completed(1, stderr="nope")) as run:
            ok, output = install_gl32_drivers(["default"])
        assert not ok
        assert output == "nope"
        assert run.call_count == 1

    def test_spawn_error(self):
        with patch.object(gl32_service.subprocess, "run", side_effect=OSError("no flatpak-spawn")):
            ok, output = install_gl32_drivers(["default"])
        assert not ok
        assert "no flatpak-spawn" in output


class FakeInstallWorker:
    """Stand-in for Gl32InstallWorker that finishes synchronously on start()."""

    result = (True, "")

    def __init__(self, drivers):
        self.drivers = drivers
        self.finished = MagicMock()
        self._handlers = []
        self.finished.connect.side_effect = self._handlers.append

    def start(self):
        for handler in self._handlers:
            handler(*self.result)

    def wait(self, timeout=0):
        return True


class TestGl32DriverDialog:
    def make_dialog(self, qtbot, settings=None):
        from gameyfin_frontend.dialogs import Gl32DriverDialog
        dialog = Gl32DriverDialog(["default"], settings=settings)
        qtbot.addWidget(dialog)
        return dialog

    def test_install_success(self, qtbot):
        FakeInstallWorker.result = (True, "")
        with patch("gameyfin_frontend.dialogs.Gl32InstallWorker", FakeInstallWorker):
            dialog = self.make_dialog(qtbot)
            dialog.install_button.click()
        assert dialog._state == "done"
        assert "Restart" in dialog.status_label.text()

    def test_install_failure_shows_manual_command(self, qtbot):
        FakeInstallWorker.result = (False, "boom")
        with patch("gameyfin_frontend.dialogs.Gl32InstallWorker", FakeInstallWorker):
            dialog = self.make_dialog(qtbot)
            dialog.install_button.click()
        assert dialog._state == "error"
        text = dialog.status_label.text()
        assert "boom" in text
        assert "org.freedesktop.Platform.GL32.default" in text

    def test_dont_ask_again_sets_setting(self, qtbot, fresh_settings):
        dialog = self.make_dialog(qtbot, settings=fresh_settings)
        dialog.never_button.click()
        assert fresh_settings.get("GF_SKIP_GL32_CHECK") == 1
