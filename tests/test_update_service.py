"""Tests for the update service (version compare, release lookup, flatpak install)."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

from gameyfin_frontend.config import APP_VERSION
from gameyfin_frontend.services import update_service
from gameyfin_frontend.services.update_service import (
    build_install_command,
    can_auto_update,
    check_latest_release,
    compare_versions,
    get_current_version,
    get_download_dir,
    get_update_asset,
    install_flatpak,
    is_running_in_flatpak,
    parse_version,
)


class TestParseVersion:
    def test_basic(self):
        assert parse_version("2.9.3") == ((2, 9, 3), "")

    def test_v_prefix(self):
        assert parse_version("v2.9.3") == ((2, 9, 3), "")

    def test_prerelease_suffix(self):
        assert parse_version("v2.9.7-dev") == ((2, 9, 7), "dev")

    def test_non_numeric_segment_treated_as_zero(self):
        assert parse_version("2.x.1") == ((2, 0, 1), "")


class TestCompareVersions:
    def test_older(self):
        assert compare_versions("v2.9.3", "2.9.7") == -1

    def test_newer(self):
        assert compare_versions("2.10.0", "2.9.9") == 1

    def test_equal_with_v_prefix(self):
        assert compare_versions("2.9.3", "v2.9.3") == 0

    def test_prerelease_older_than_release(self):
        assert compare_versions("2.9.7-dev", "2.9.7") == -1

    def test_prerelease_newer_than_older_release(self):
        assert compare_versions("2.9.7-dev", "2.9.6") == 1

    def test_missing_segment_padded_with_zero(self):
        assert compare_versions("2.9", "2.9.0") == 0


class TestGetCurrentVersion:
    def test_returns_app_version(self):
        assert get_current_version() == APP_VERSION


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


class TestGetUpdateAsset:
    def test_picks_flatpak_on_linux(self):
        with patch.object(update_service.sys, "platform", "linux"):
            asset = get_update_asset(make_release())
        assert asset["name"].endswith(".flatpak")

    def test_picks_exe_on_windows(self):
        with patch.object(update_service.sys, "platform", "win32"):
            asset = get_update_asset(make_release())
        assert asset["name"].endswith(".exe")

    def test_missing_asset_returns_none(self):
        with patch.object(update_service.sys, "platform", "linux"):
            assert get_update_asset({"tag_name": "v1.0", "assets": []}) is None

    def test_missing_tag_returns_none(self):
        assert get_update_asset({"assets": []}) is None


class TestGetDownloadDir:
    def test_under_config_dir(self):
        assert get_download_dir("/cfg") == os.path.join("/cfg", "updates")


class TestIsRunningInFlatpak:
    def test_false_without_env(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert is_running_in_flatpak() is False

    def test_true_with_env(self, monkeypatch):
        monkeypatch.setenv("FLATPAK_ID", "org.gameyfin.Gameyfin-Desktop")
        assert is_running_in_flatpak() is True


class TestCanAutoUpdate:
    def test_flatpak_linux(self, monkeypatch):
        monkeypatch.setenv("FLATPAK_ID", "org.gameyfin.Gameyfin-Desktop")
        with patch.object(update_service.sys, "platform", "linux"):
            assert can_auto_update() is True

    def test_non_flatpak_linux(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        with patch.object(update_service.sys, "platform", "linux"):
            assert can_auto_update() is False

    def test_windows(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        with patch.object(update_service.sys, "platform", "win32"):
            assert can_auto_update() is False


class TestBuildInstallCommand:
    def test_native(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert build_install_command("/tmp/x.flatpak") == [
            "flatpak", "install", "--user", "/tmp/x.flatpak", "-y",
        ]

    def test_inside_flatpak_uses_spawn(self, monkeypatch):
        monkeypatch.setenv("FLATPAK_ID", "org.gameyfin.Gameyfin-Desktop")
        assert build_install_command("/tmp/x.flatpak") == [
            "flatpak-spawn", "--host", "flatpak", "install",
            "--user", "/tmp/x.flatpak", "-y",
        ]


class TestInstallFlatpak:
    def test_success(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        result = MagicMock(returncode=0, stdout="Installed\n", stderr="")
        with patch.object(update_service.subprocess, "run", return_value=result) as mock_run:
            ok, output = install_flatpak("/tmp/x.flatpak")
        assert ok is True
        assert "Installed" in output
        assert mock_run.call_args[0][0][0] == "flatpak"

    def test_failure_returncode(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        result = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch.object(update_service.subprocess, "run", return_value=result):
            ok, output = install_flatpak("/tmp/x.flatpak")
        assert ok is False
        assert "boom" in output

    def test_spawn_failure(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        with patch.object(
            update_service.subprocess, "run", side_effect=OSError("no flatpak")
        ):
            ok, output = install_flatpak("/tmp/x.flatpak")
        assert ok is False
        assert "no flatpak" in output

    def test_timeout(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        with patch.object(
            update_service.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("flatpak", 600),
        ):
            ok, output = install_flatpak("/tmp/x.flatpak")
        assert ok is False


class TestCheckLatestRelease:
    def test_returns_json(self):
        response = MagicMock()
        response.json.return_value = {"tag_name": "v2.9.4"}
        response.raise_for_status.return_value = None
        with patch.object(update_service.requests, "get", return_value=response) as mock_get:
            release = check_latest_release()
        assert release == {"tag_name": "v2.9.4"}
        assert "User-Agent" in mock_get.call_args.kwargs["headers"]

    def test_http_error_raises(self):
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("403")
        with patch.object(update_service.requests, "get", return_value=response):
            with pytest.raises(requests.exceptions.HTTPError):
                check_latest_release()
