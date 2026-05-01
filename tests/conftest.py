import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    """Configure Qt for headless testing before any QCoreApplication is created."""
    # Must be set before any Qt import that creates a QCoreApplication
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Force Qt to use software rendering for WebEngine in headless mode
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

    # Import QtWebEngineWidgets before pytest-qt creates QCoreApplication.
    # This is required because gameyfin_frontend imports QWebEngineView at package level,
    # and QtWebEngine requires special initialization before any QCoreApplication exists.
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineWidgets  # noqa: F401
    except ImportError:
        pass  # Not available in all environments


@pytest.fixture()
def tmp_app_data(tmp_path):
    """Provide a temporary app data directory that mimics QStandardPaths.AppDataLocation."""
    app_data = tmp_path / "app_data"
    app_data.mkdir()
    old_env = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    try:
        yield str(app_data)
    finally:
        if old_env is not None:
            os.environ["HOME"] = old_env
        else:
            os.environ.pop("HOME", None)


@pytest.fixture()
def settings_file(tmp_app_data):
    """Return the path to a settings.json inside the temporary app data dir."""
    return os.path.join(tmp_app_data, "settings.json")


@pytest.fixture()
def umu_cache_file(tmp_app_data):
    """Return the path to a UMU cache JSON file inside the temporary app data dir."""
    return os.path.join(tmp_app_data, "umu_cache.json")


@pytest.fixture()
def sample_umu_entries():
    """Return a list of sample UMU database entries for testing."""
    return [
        {"umu_id": "UMU-001", "title": "Baldur's Gate II", "store": "steam"},
        {"umu_id": "UMU-002", "title": "Baldur's Gate III", "store": "steam"},
        {"umu_id": "UMU-003", "title": "The Witcher 3", "store": "none"},
        {"umu_id": "UMU-004", "title": "Divinity: Original Sin 2", "store": "gog"},
        {"umu_id": "UMU-005", "title": "Divinity: Original Sin 2 - Definitive Edition", "store": "steam"},
    ]


@pytest.fixture()
def valid_desktop_file(tmp_path):
    """Create a valid .desktop file and return its path."""
    desktop_content = """[Desktop Entry]
Name=TestGame
Exec=/path/to/game.exe
Icon=test-icon
Type=Application
Categories=Game;
"""
    path = tmp_path / "testgame.desktop"
    path.write_text(desktop_content)
    return str(path)


@pytest.fixture()
def desktop_file_missing_header(tmp_path):
    """Create a .desktop file without the [Desktop Entry] header."""
    desktop_content = """Name=TestGameNoHeader
Exec=/path/to/game.exe
Icon=test-icon
"""
    path = tmp_path / "testgame-noheader.desktop"
    path.write_text(desktop_content)
    return str(path)


@pytest.fixture()
def invalid_desktop_file(tmp_path):
    """Create a file that is not a valid .desktop file."""
    path = tmp_path / "not-a-desktop.txt"
    path.write_text("This is not a desktop file\nrandom content\n")
    return str(path)
