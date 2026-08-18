"""Update checking and installation for the Gameyfin Desktop client.

Queries the GitHub releases API for the latest stable release, compares it
against the running version, and installs a downloaded ``.flatpak`` bundle
with the same command as the README:

    flatpak install --user Gameyfin-Desktop-vX.X.X.flatpak -y

When the app itself runs inside a Flatpak sandbox the install is delegated
to the host via ``flatpak-spawn --host``.
"""

import logging
import os
import subprocess
import sys

import requests

from gameyfin_frontend.config import APP_VERSION, GITHUB_LATEST_RELEASE_URL

logger = logging.getLogger(__name__)

# GitHub's API requires a User-Agent header
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"Gameyfin-Desktop/{APP_VERSION}",
}

# Seconds to wait on the GitHub API / flatpak install
REQUEST_TIMEOUT = 15
INSTALL_TIMEOUT = 600


def is_running_in_flatpak() -> bool:
    """Return True when running inside a Flatpak sandbox."""
    return bool(os.environ.get("FLATPAK_ID"))


def can_auto_update() -> bool:
    """Return True when this platform has an auto-update path.

    Flatpak installs via ``flatpak-spawn --host``.
    Windows and source installs (Linux) have no auto-update path — they
    see a link to the GitHub releases page instead.
    """
    return sys.platform.startswith("linux") and is_running_in_flatpak()


def get_current_version() -> str:
    """Return the version of the running application."""
    return APP_VERSION


def parse_version(version: str) -> tuple[tuple[int, ...], str]:
    """Parse a version string like ``v2.9.7-dev`` into ``((2, 9, 7), "dev")``.

    Non-numeric segments are treated as 0. A missing prerelease suffix
    yields an empty string.
    """
    text = version.strip().lstrip("vV")
    prerelease = ""
    if "-" in text:
        text, prerelease = text.split("-", 1)
    parts = []
    for segment in text.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return (tuple(parts), prerelease)


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings.

    Returns -1 if *a* is older, 0 if equivalent, 1 if *a* is newer.
    When the numeric parts are equal, a version with a prerelease suffix
    (e.g. ``2.9.7-dev``) is considered older than the plain release.
    """
    a_nums, a_pre = parse_version(a)
    b_nums, b_pre = parse_version(b)
    width = max(len(a_nums), len(b_nums))
    a_nums += (0,) * (width - len(a_nums))
    b_nums += (0,) * (width - len(b_nums))
    if a_nums != b_nums:
        return -1 if a_nums < b_nums else 1
    if bool(a_pre) != bool(b_pre):
        return -1 if a_pre else 1
    return 0


def check_latest_release(timeout: int = REQUEST_TIMEOUT) -> dict:
    """Fetch the latest stable (non-prerelease) release from GitHub.

    Returns the release payload (``tag_name``, ``assets``, ...). Raises
    ``requests.exceptions.RequestException`` on network or HTTP errors.
    """
    response = requests.get(
        GITHUB_LATEST_RELEASE_URL, headers=_GITHUB_HEADERS, timeout=timeout
    )
    response.raise_for_status()
    return response.json()


def get_update_asset(release: dict) -> dict | None:
    """Pick the release asset matching this platform.

    Linux expects ``Gameyfin-Desktop-<tag>.flatpak``, Windows the ``.exe``.
    Returns the asset dict (including ``browser_download_url``) or None.
    """
    tag = release.get("tag_name", "")
    if not tag:
        return None
    extension = ".flatpak" if sys.platform.startswith("linux") else ".exe"
    expected_name = f"Gameyfin-Desktop-{tag}{extension}"
    for asset in release.get("assets", []):
        if asset.get("name") == expected_name:
            return asset
    return None


def get_download_dir(config_dir: str) -> str:
    """Directory for downloaded update bundles.

    Lives under the config dir (which is under $HOME) so the host can see
    the file even when the app runs inside the Flatpak sandbox.
    """
    return os.path.join(config_dir, "updates")


def build_install_command(flatpak_path: str) -> list[str]:
    """Build the flatpak install command (same as the README).

    Inside the sandbox the install must run on the host, so the command is
    wrapped in ``flatpak-spawn --host``.
    """
    if is_running_in_flatpak():
        return [
            "flatpak-spawn", "--host", "flatpak", "install",
            "--user", flatpak_path, "-y",
        ]
    return ["flatpak", "install", "--user", flatpak_path, "-y"]


def install_flatpak(flatpak_path: str) -> tuple[bool, str]:
    """Install a downloaded ``.flatpak`` bundle.

    Runs synchronously — call from a worker thread, not the GUI thread.
    Returns ``(success, output)``.
    """
    command = build_install_command(flatpak_path)
    logger.info("Installing update: %s", " ".join(command))
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("Flatpak install failed: %s", exc)
        return False, str(exc)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        logger.error("Flatpak install exited %d: %s", result.returncode, output)
        return False, output
    return True, output
