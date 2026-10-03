"""Download, list and remove Proton builds (a small ProtonUp-Qt).

Builds are installed into Steam's ``compatibilitytools.d`` — the same place
umu-run puts the GE-Proton it downloads for ``PROTONPATH=GE-Proton`` and where
ProtonUp-Qt installs to — so they are shared with Steam and umu. A build is
used by setting ``PROTONPATH`` to its absolute directory.

Release metadata comes from the GitHub releases API of each source. Archives
are checked against the ``.sha512sum`` published next to them and extracted as
a stream, so a multi-GB build is only read once.
"""

import hashlib
import logging
import os
import shutil
import tarfile
from dataclasses import dataclass

import requests

from gameyfin_frontend.config import APP_VERSION, DEFAULT_PROTON

logger = logging.getLogger(__name__)

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"Gameyfin-Desktop/{APP_VERSION}",
}
REQUEST_TIMEOUT = 15

# Releases fetched per source (one API page)
RELEASES_PER_PAGE = 30

# Where new builds go: Steam's native compatibility tools directory
INSTALL_DIR = os.path.join("~", ".local", "share", "Steam", "compatibilitytools.d")

# Every directory Steam (native or Flatpak) reads compatibility tools from.
# ~/.steam/root is normally a symlink to ~/.local/share/Steam; duplicates are
# collapsed by real path.
COMPAT_TOOL_DIRS = [
    INSTALL_DIR,
    os.path.join("~", ".steam", "root", "compatibilitytools.d"),
    os.path.join("~", ".var", "app", "com.valvesoftware.Steam", "data", "Steam", "compatibilitytools.d"),
]

# Prefix of temporary files/directories created while installing
_TEMP_PREFIX = ".gameyfin-"


@dataclass(frozen=True)
class ProtonSource:
    """A GitHub repository publishing Proton builds."""

    name: str
    repo: str
    description: str


SOURCES = [
    ProtonSource("GE-Proton", "GloriousEggroll/proton-ge-custom",
                 "Proton with extra patches and fixes by GloriousEggroll."),
    ProtonSource("Proton-CachyOS", "CachyOS/proton-cachyos",
                 "CachyOS' Proton build with performance patches."),
    ProtonSource("UMU-Proton", "Open-Wine-Components/umu-proton",
                 "Valve's Proton packaged for umu-launcher."),
]


@dataclass
class ProtonRelease:
    """One downloadable build of a source."""

    tag: str
    name: str
    url: str
    size: int
    checksum_url: str | None
    published: str = ""
    prerelease: bool = False
    # Archive file name without extension; the build's directory name
    stem: str = ""


@dataclass
class InstalledProton:
    """A Proton build found in a compatibility tools directory."""

    name: str
    path: str


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def install_dir() -> str:
    """Return the directory new builds are installed into."""
    return _expand(INSTALL_DIR)


def archive_stem(file_name: str) -> str:
    """Strip the archive extension from *file_name*."""
    for ext in (".tar.gz", ".tar.xz", ".tar.zst", ".tgz", ".tar"):
        if file_name.endswith(ext):
            return file_name[: -len(ext)]
    return file_name


def cpu_supports_x86_64_v3() -> bool:
    """Return True when the CPU implements the x86-64-v3 feature level."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("flags"):
                    flags = set(line.split(":", 1)[1].split())
                    return {"avx2", "bmi2", "fma", "movbe"} <= flags
    except OSError:
        pass
    return False


def _pick_asset(assets: list[dict], prefer_v3: bool) -> dict | None:
    """Choose the x86_64 archive among a release's assets.

    Releases may carry aarch64/arm64 builds and, for CachyOS, both a baseline
    ``x86_64`` and an ``x86_64_v3`` build; the latter is taken when the CPU
    supports it.
    """
    archives = [
        a for a in assets
        if archive_stem(a.get("name", "")) != a.get("name", "")
        and not any(arch in a["name"] for arch in ("aarch64", "arm64"))
    ]
    if not archives:
        return None
    v3 = [a for a in archives if "x86_64_v3" in a["name"]]
    baseline = [a for a in archives if "x86_64_v3" not in a["name"]]
    if prefer_v3 and v3:
        return v3[0]
    return baseline[0] if baseline else archives[0]


def fetch_releases(source: ProtonSource, prefer_v3: bool | None = None) -> list[ProtonRelease]:
    """Return the newest releases of *source*, newest first.

    Raises:
        requests.exceptions.RequestException: On network or HTTP errors.
    """
    if prefer_v3 is None:
        prefer_v3 = cpu_supports_x86_64_v3()
    response = requests.get(
        f"https://api.github.com/repos/{source.repo}/releases",
        params={"per_page": RELEASES_PER_PAGE},
        headers=_GITHUB_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    releases: list[ProtonRelease] = []
    for release in response.json():
        if release.get("draft"):
            continue
        assets = release.get("assets") or []
        asset = _pick_asset(assets, prefer_v3)
        if asset is None:
            continue
        stem = archive_stem(asset["name"])
        checksum = next(
            (a for a in assets if a.get("name") == f"{stem}.sha512sum"), None
        )
        releases.append(ProtonRelease(
            tag=release.get("tag_name", stem),
            name=stem,
            url=asset["browser_download_url"],
            size=int(asset.get("size") or 0),
            checksum_url=checksum["browser_download_url"] if checksum else None,
            published=(release.get("published_at") or "")[:10],
            prerelease=bool(release.get("prerelease")),
            stem=stem,
        ))
    return releases


def list_installed() -> list[InstalledProton]:
    """Return the Proton builds in every compatibility tools directory, sorted by name."""
    found: list[InstalledProton] = []
    seen: set[str] = set()
    for directory in COMPAT_TOOL_DIRS:
        directory = _expand(directory)
        if not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError as e:
            logger.warning("Cannot list %s: %s", directory, e)
            continue
        for entry in entries:
            if entry.startswith(_TEMP_PREFIX):
                continue
            path = os.path.join(directory, entry)
            # A Proton build has a "proton" launcher script at its root
            if not os.path.isfile(os.path.join(path, "proton")):
                continue
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            found.append(InstalledProton(name=entry, path=path))
    found.sort(key=lambda p: _natural_key(p.name), reverse=True)
    return found


def _natural_key(text: str) -> list:
    """Sort key that orders embedded numbers numerically (GE-Proton10-9 < GE-Proton10-10)."""
    key: list = []
    number = ""
    for char in text:
        if char.isdigit():
            number += char
            continue
        if number:
            key.append((1, int(number), ""))
            number = ""
        key.append((0, 0, char.lower()))
    if number:
        key.append((1, int(number), ""))
    return key


def is_installed(release: ProtonRelease, installed: list[InstalledProton]) -> bool:
    """Return True when *release* is among *installed* (by directory name)."""
    names = {p.name for p in installed}
    return release.stem in names or release.tag in names


def remove(path: str) -> None:
    """Delete an installed Proton build.

    Only directories directly inside a known compatibility tools directory are
    accepted, so a bad path can never remove anything else.

    Raises:
        ValueError: If *path* is not an installed Proton build.
        OSError: If deleting fails.
    """
    parent = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    allowed = {os.path.realpath(_expand(d)) for d in COMPAT_TOOL_DIRS}
    if parent not in allowed or not os.path.isfile(os.path.join(path, "proton")):
        raise ValueError(f"Not an installed Proton build: {path}")
    if os.path.islink(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)
    logger.info("Removed Proton build %s", path)


def display_name(value: str | None) -> str:
    """Return a short label for a ``PROTONPATH`` value."""
    if not value:
        return DEFAULT_PROTON
    if os.path.isabs(value):
        return os.path.basename(value.rstrip(os.sep)) or value
    return value


class InstallCancelled(Exception):
    """Raised inside an install when the user cancelled it."""


def parse_checksum(text: str, file_name: str) -> str | None:
    """Return the SHA-512 for *file_name* from a ``sha512sum`` file."""
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        digest = parts[0].lower()
        name = parts[-1].lstrip("*") if len(parts) > 1 else file_name
        if os.path.basename(name) == file_name and len(digest) == 128:
            return digest
    return None


def install_release(
    release: ProtonRelease,
    progress=None,
    is_cancelled=None,
    target_dir: str | None = None,
    chunk_size: int = 1 << 20,
) -> str:
    """Download, verify and extract *release*; return the installed directory.

    The archive is downloaded next to the target, checked against its SHA-512,
    then extracted into a temporary directory that is moved into place only
    once complete, so an interrupted install never leaves a half-extracted
    build that would show up as installed.

    Args:
        release: Release to install.
        progress: ``progress(stage, done, total)`` callback; stage is
            ``"download"`` or ``"extract"``, sizes are in bytes.
        is_cancelled: Callable returning True to abort.
        target_dir: Override for the install directory (tests).
        chunk_size: Download chunk size.

    Raises:
        InstallCancelled: When *is_cancelled* turned True.
        requests.exceptions.RequestException: On network errors.
        ValueError: On a checksum mismatch or an unusable archive.
        OSError: On file system errors.
    """
    target_dir = target_dir or install_dir()
    os.makedirs(target_dir, exist_ok=True)
    report = progress or (lambda *_: None)
    cancelled = is_cancelled or (lambda: False)

    file_name = os.path.basename(release.url.split("?", 1)[0])
    archive_path = os.path.join(target_dir, f"{_TEMP_PREFIX}{file_name}.part")
    staging_dir = os.path.join(target_dir, f"{_TEMP_PREFIX}{release.stem}.extract")

    try:
        expected = None
        if release.checksum_url:
            response = requests.get(release.checksum_url, headers=_GITHUB_HEADERS,
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            expected = parse_checksum(response.text, file_name)
            if expected is None:
                logger.warning("No checksum for %s in %s", file_name, release.checksum_url)

        digest = hashlib.sha512()
        with requests.get(release.url, stream=True, headers=_GITHUB_HEADERS,
                          timeout=(REQUEST_TIMEOUT, 300)) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or release.size or 0)
            done = 0
            with open(archive_path, "wb") as f:
                for chunk in response.iter_content(chunk_size):
                    if cancelled():
                        raise InstallCancelled()
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    report("download", done, total)

        if expected and digest.hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {file_name}; the download is corrupt.")

        shutil.rmtree(staging_dir, ignore_errors=True)
        os.makedirs(staging_dir)
        total = os.path.getsize(archive_path)
        with open(archive_path, "rb") as raw, tarfile.open(fileobj=raw, mode="r|*") as tar:
            for member in tar:
                if cancelled():
                    raise InstallCancelled()
                tar.extract(member, staging_dir, filter="tar")
                report("extract", raw.tell(), total)

        top_level = [e for e in os.listdir(staging_dir) if not e.startswith(".")]
        if len(top_level) == 1 and os.path.isdir(os.path.join(staging_dir, top_level[0])):
            source, name = os.path.join(staging_dir, top_level[0]), top_level[0]
        else:
            source, name = staging_dir, release.stem
        if not os.path.isfile(os.path.join(source, "proton")):
            raise ValueError(f"{file_name} does not contain a Proton build.")

        destination = os.path.join(target_dir, name)
        if os.path.exists(destination):
            shutil.rmtree(destination)
        os.replace(source, destination)
        logger.info("Installed %s to %s", release.name, destination)
        return destination
    finally:
        try:
            os.remove(archive_path)
        except OSError:
            pass
        shutil.rmtree(staging_dir, ignore_errors=True)
