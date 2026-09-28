"""Detection and installation of the 32-bit GL driver extension (Flatpak only).

32-bit Windows games and launchers (e.g. Battle.net) need 32-bit Mesa/Vulkan
drivers unless WOW64 is enabled. Those come from the Flathub
``org.freedesktop.Platform.GL32.<driver>`` extension, which Flatpak does not
fetch for an app installed from a ``.flatpak`` bundle: related refs are only
looked up in the app's own (URL-less) origin remote
(https://github.com/flatpak/flatpak/issues/3828).

Inside the sandbox every active GL driver is mounted as
``<GL dir>/<driver>/lib``, so comparing the 64-bit and 32-bit GL dirs tells
which 32-bit drivers are missing without asking the host. Installing runs on
the host via ``flatpak-spawn --host``.
"""

import logging
import os
import subprocess

from gameyfin_frontend.config import FDO_RUNTIME_VERSION
from gameyfin_frontend.services.update_service import is_running_in_flatpak

logger = logging.getLogger(__name__)

GL64_DIR = "/usr/lib/x86_64-linux-gnu/GL"
GL32_DIR = "/app/lib/i386-linux-gnu/GL"
GL32_REF_PREFIX = "org.freedesktop.Platform.GL32."
FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"

# Mesa ("default") follows the runtime version; NVIDIA drivers use branch 1.4
GL32_BRANCHES = (FDO_RUNTIME_VERSION, "1.4")

INSTALL_TIMEOUT = 600


def gl_drivers_in(gl_dir: str) -> set[str]:
    """Return the GL drivers mounted in *gl_dir*.

    A driver is a subdirectory whose ``lib/`` holds shared libraries. The
    other entries (``glvnd``, ``vulkan``, ...) are merge-dirs, and an empty
    driver dir can be left behind by the build.
    """
    drivers = set()
    try:
        entries = os.listdir(gl_dir)
    except OSError:
        return drivers
    for name in entries:
        lib_dir = os.path.join(gl_dir, name, "lib")
        try:
            if any(".so" in f for f in os.listdir(lib_dir)):
                drivers.add(name)
        except OSError:
            continue
    return drivers


def missing_gl32_drivers(gl64_dir: str = GL64_DIR, gl32_dir: str = GL32_DIR) -> list[str]:
    """Return the active GL drivers that have no 32-bit counterpart.

    Always empty outside Flatpak, where 32-bit drivers are the distro's job.
    """
    if not is_running_in_flatpak():
        return []
    # "host" means the host's own GL stack is used; there's nothing to install
    active = gl_drivers_in(gl64_dir) - {"host"}
    return sorted(active - gl_drivers_in(gl32_dir))


def _run_host(args: list[str]) -> tuple[bool, str]:
    command = ["flatpak-spawn", "--host", "flatpak", *args]
    logger.info("Running: %s", " ".join(command))
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, output


def install_gl32_drivers(drivers: list[str]) -> tuple[bool, str]:
    """Install the 32-bit GL extension for each driver in *drivers*.

    Runs synchronously — call from a worker thread, not the GUI thread.
    Returns ``(success, output)``; the app has to be restarted before
    Flatpak mounts the new extension.
    """
    # The app is installed per-user, so the extension goes there as well
    ok, output = _run_host(["remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_URL])
    if not ok:
        logger.error("Could not add the Flathub remote: %s", output)
        return False, output

    outputs = []
    for driver in drivers:
        for branch in GL32_BRANCHES:
            ref = f"{GL32_REF_PREFIX}{driver}//{branch}"
            ok, output = _run_host(["install", "--user", "-y", "--noninteractive", "flathub", ref])
            if ok:
                break
        outputs.append(output)
        if not ok:
            logger.error("Installing 32-bit GL driver '%s' failed: %s", driver, output)
            return False, "\n".join(outputs)
    return True, "\n".join(outputs)
