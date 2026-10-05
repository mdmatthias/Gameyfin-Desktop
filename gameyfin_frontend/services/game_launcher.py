"""Game launching — platform-specific execution via QProcess."""

from __future__ import annotations

import logging
import os
import re
import signal
from typing import Any

from PyQt6.QtCore import QProcess, QProcessEnvironment

from gameyfin_frontend.utils import build_umu_env_prefix
from gameyfin_frontend.config import DEFAULT_PROTON

logger = logging.getLogger(__name__)


def log_output_as_it_arrives(process: QProcess) -> None:
    """Stream the process's merged stdout/stderr to the log as it arrives.

    QProcess discards output that nothing reads, so umu-run/game crashes
    (e.g. Python tracebacks) would otherwise vanish silently, leaving only
    the exit code in the logs. Logging as chunks arrive (rather than only
    buffering for a final dump) also gives visibility into long-running
    steps like Proton/runtime downloads while they're in progress.
    """
    output_lines: list[str] = []

    def _on_ready_read() -> None:
        chunk = bytes(process.readAllStandardOutput()).decode(errors="replace")
        output_lines.append(chunk)
        for line in chunk.splitlines():
            if line.strip():
                logger.info("[umu-run] %s", line)

    process.readyReadStandardOutput.connect(_on_ready_read)

    def _on_finished(exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if exit_code != 0 and output_lines:
            logger.error("Process exited with code %s.", exit_code)

    process.finished.connect(_on_finished)


def launch_script(script_path: str, parent: Any = None) -> tuple[QProcess, Any]:
    """Run a generated launch script, showing a loading dialog while Proton starts.

    Args:
        script_path: Full path to the ``.sh`` launch script.
        parent: Parent widget for the process and the dialog.

    Returns:
        The started process and the loading dialog. Callers keep both
        referenced so neither is garbage-collected while the game runs.

    Raises:
        OSError: If the script could not be started.
    """
    # Imported here: dialogs pulls in services at import time (circular import)
    from gameyfin_frontend.dialogs import LaunchLoadingDialog

    # Use the script filename (without .sh) as the display name
    script_name = os.path.splitext(os.path.basename(script_path))[0]
    loading_dialog = LaunchLoadingDialog(script_name, parent=parent)
    loading_dialog.show()

    process = QProcess(parent)
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    process.setWorkingDirectory(os.path.dirname(script_path))
    process.start(script_path, [])
    if not process.waitForStarted():
        loading_dialog.close()
        raise OSError(f"Failed to start {script_path}")
    log_output_as_it_arrives(process)
    return process, loading_dialog


def _process_table(proc_dir: str = "/proc") -> dict[int, int]:
    """Return every visible process as ``pid -> parent pid``."""
    table: dict[int, int] = {}
    try:
        entries = os.listdir(proc_dir)
    except OSError:
        return table
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_dir, entry, "stat"), "rb") as f:
                stat = f.read()
            # The command name is in parentheses and may itself contain spaces
            # or parentheses, so the fields are counted from the last ")"
            fields = stat[stat.rindex(b")") + 2:].split()
            table[int(entry)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    return table


def _uses_prefix(pid: int, wine_prefix: str, proc_dir: str = "/proc") -> bool:
    """Return True when *pid* runs with ``WINEPREFIX`` set to *wine_prefix*."""
    try:
        with open(os.path.join(proc_dir, str(pid), "environ"), "rb") as f:
            environ = f.read().split(b"\0")
    except OSError:
        return False
    for var in environ:
        if var.startswith(b"WINEPREFIX="):
            value = os.fsdecode(var[len(b"WINEPREFIX="):])
            return os.path.normpath(value) == wine_prefix
    return False


def find_game_processes(root_pid: int | None, wine_prefix: str | None,
                        proc_dir: str = "/proc") -> set[int]:
    """Return the processes that belong to a running game.

    That is the launch script (*root_pid*) with all its descendants, plus every
    process using *wine_prefix* — wineserver and the game itself are not always
    children of the script (a launcher can hand off to the game, and wineserver
    daemonizes), but they all inherit the prefix's ``WINEPREFIX``.
    """
    table = _process_table(proc_dir)
    # Never signal ourselves or the processes we run under
    protected: set[int] = set()
    pid = os.getpid()
    while pid > 1 and pid not in protected:
        protected.add(pid)
        pid = table.get(pid, 0)

    found: set[int] = set()
    if root_pid and root_pid in table:
        children: dict[int, list[int]] = {}
        for child, parent in table.items():
            children.setdefault(parent, []).append(child)
        stack = [root_pid]
        while stack:
            pid = stack.pop()
            if pid not in found:
                found.add(pid)
                stack.extend(children.get(pid, []))

    if wine_prefix:
        prefix = os.path.normpath(wine_prefix)
        found.update(pid for pid in table if pid not in found and _uses_prefix(pid, prefix, proc_dir))

    return found - protected


def stop_game(root_pid: int | None, wine_prefix: str | None,
              sig: int = signal.SIGTERM) -> set[int]:
    """Send *sig* to every process of a running game and return their pids.

    See :func:`find_game_processes` for what counts as the game's processes.
    """
    pids = find_game_processes(root_pid, wine_prefix)
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    logger.info("Sent signal %s to %d game process(es) (prefix %s)", sig, len(pids), wine_prefix)
    return pids


class GameLauncher:
    """Launches games via QProcess — Windows direct exec, Linux via UMU."""

    def start_windows(self, launcher_to_run: str) -> QProcess | None:
        """Launch a game executable directly via QProcess (Windows path).

        Args:
            launcher_to_run: Absolute path to the .exe to launch.

        Returns:
            The QProcess instance, or ``None`` if launch failed.
        """
        try:
            logger.info("Executing (Windows): %s", launcher_to_run)
            process = QProcess()
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.setProgram(launcher_to_run)
            process.setWorkingDirectory(os.path.dirname(launcher_to_run))
            process.start()
            if not process.waitForStarted():
                logger.info("Launch failed (QProcess failed to start).")
                return None

            log_output_as_it_arrives(process)
            return process
        except OSError as e:
            logger.error("Launch failed: %s", e)
            return None

    def start_linux(
        self,
        launcher_to_run: str,
        target_dir: str,
        install_config: dict[str, Any],
        wine_prefix_path: str,
        proton_path: str = DEFAULT_PROTON,
    ) -> QProcess | None:
        """Launch a game via UMU environment prefix and umu-run on Linux.

        Builds the command string with ``build_umu_env_prefix`` and executes it
        via ``/bin/sh -c`` with ``exec`` for proper signal forwarding.

        Args:
            launcher_to_run: Path to the game executable.
            target_dir: Download target directory (unused, for future use).
            install_config: Dict of environment variables and UMU settings.
            wine_prefix_path: Path to the Wine prefix directory.
            proton_path: Proton version string. Defaults to "GE-Proton".

        Returns:
            The QProcess instance, or ``None`` if launch failed.
        """
        try:
            config = install_config or {}

            if not wine_prefix_path:
                raise ValueError("Wineprefix path was not set.")

            launcher_dir = os.path.dirname(launcher_to_run)

            logger.info("[Install] Applying user environment configuration:")
            for key, value in config.items():
                logger.info("  %s=%s", key, value)

            env_prefix = build_umu_env_prefix(proton_path, wine_prefix_path, config)

            game_args = config.get("GAME_ARGS", "")
            if game_args:
                command = f'exec umu-run "{launcher_to_run}" {game_args}'
            else:
                command = f'exec umu-run "{launcher_to_run}"'

            logger.info("Executing: /bin/sh -c \"%s\"", command)
            process = QProcess()
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.setWorkingDirectory(launcher_dir)

            # Set UMU environment variables directly on the process instead of
            # wrapping in /bin/sh -c "VAR=val umu-run ...".  This avoids all
            # shell quoting issues — game args appear exactly as the user typed
            # them, with no single-quote wrapping.
            env = QProcessEnvironment.systemEnvironment()
            for match in re.finditer(r'(\w+)="([^"]*)"', env_prefix):
                env.insert(match.group(1), match.group(2))
            process.setProcessEnvironment(env)

            process.start("/bin/sh", ["-c", command])

            if not process.waitForStarted():
                logger.info("Launch failed (QProcess failed to start).")
                return None

            log_output_as_it_arrives(process)
            return process
        except (ValueError, OSError) as e:
            logger.error("Launch failed: %s", e)
            return None
