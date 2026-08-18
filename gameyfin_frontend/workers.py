import logging
import os
import time
from typing import Any

import requests
from stream_unzip import stream_unzip
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread

from .config import DOWNLOAD_CHUNK_SIZE, PROGRESS_SIGNAL_INTERVAL
from .services.update_service import check_latest_release, install_flatpak
from .utils import sanitize_name

logger = logging.getLogger(__name__)


class StreamDownloadWorker(QObject):
    progress = pyqtSignal(int)
    current_file = pyqtSignal(str)
    bytes_received = pyqtSignal("long long", "long long")
    finished = pyqtSignal()
    error = pyqtSignal(str)
    _path_updated = pyqtSignal(str)

    def __init__(self, url: str, target_dir: str, cookies: dict[str, Any] | None = None, estimated_total: int = 0, bandwidth_limit: int = 0) -> None:
        """Initialize a background worker that streams a URL to a directory while unzipping.

        Emits progress, bytes_received, current_file, finished, and error signals.

        Args:
            url: The URL to download from.
            target_dir: The directory to extract files into.
            cookies: Optional dict of cookies to include in the request.
            estimated_total: Fallback total byte count if Content-Length is missing.
            bandwidth_limit: Max download speed in bytes/sec. 0 means unlimited.
        """
        super().__init__()
        self.url = url
        self.target_dir = target_dir
        self.cookies = cookies or {}
        self.estimated_total = estimated_total
        self.bandwidth_limit = bandwidth_limit
        self._is_running = True
        self._cancelled = False
        self._session = requests.Session()
        self._response = None

    @pyqtSlot()
    def run(self) -> None:
        """Execute the streaming download with unzip, path traversal protection, and progress signals."""
        try:
            real_target = os.path.realpath(self.target_dir)
            os.makedirs(self.target_dir, exist_ok=True)

            self._response = self._session.get(
                self.url, stream=True, cookies=self.cookies, timeout=30
            )
            self._response.raise_for_status()

            content_length = int(self._response.headers.get('content-length', 0))
            received = 0
            last_signal_time = 0.0

            def http_chunks():
                nonlocal received, last_signal_time
                chunk_start = time.monotonic()
                chunk_bytes = 0
                for chunk in self._response.iter_content(DOWNLOAD_CHUNK_SIZE):
                    if not self._is_running:
                        return
                    received += len(chunk)
                    chunk_bytes += len(chunk)
                    now = time.monotonic()
                    if now - last_signal_time >= PROGRESS_SIGNAL_INTERVAL:
                        # Re-read estimated_total on every tick: when the server omits
                        # Content-Length, a slower fallback size lookup may still be
                        # in flight and can update this after run() already started.
                        total = content_length or self.estimated_total
                        self.bytes_received.emit(received, total)
                        if total > 0:
                            self.progress.emit(min(int(received / total * 100), 99))
                        last_signal_time = now
                    # Bandwidth throttling: sleep if we're going too fast
                    if self.bandwidth_limit > 0:
                        elapsed = time.monotonic() - chunk_start
                        min_elapsed = chunk_bytes / self.bandwidth_limit
                        if min_elapsed > elapsed:
                            time.sleep(min_elapsed - elapsed)
                        chunk_start = time.monotonic()
                        chunk_bytes = 0
                    yield chunk

            for file_name, _file_size, unzipped_chunks in stream_unzip(http_chunks()):
                if not self._is_running:
                    for _ in unzipped_chunks:
                        pass
                    self.error.emit("Download cancelled by user.")
                    return

                name_str = file_name.decode('utf-8', errors='replace')
                self.current_file.emit(f"Extracting: {name_str}")

                target_path = os.path.realpath(os.path.join(self.target_dir, name_str))
                if not target_path.startswith(real_target + os.sep) and target_path != real_target:
                    for _ in unzipped_chunks:
                        pass
                    continue

                if name_str.endswith('/'):
                    os.makedirs(target_path, exist_ok=True)
                    for _ in unzipped_chunks:
                        pass
                    continue

                parent_dir = os.path.dirname(target_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)

                with open(target_path, 'wb') as f:
                    for chunk in unzipped_chunks:
                        if not self._is_running:
                            self.error.emit("Download cancelled by user.")
                            return
                        f.write(chunk)

            new_path = self._rename_extracted_folder()
            if new_path:
                self._path_updated.emit(new_path)

            self.progress.emit(100)
            self.finished.emit()

        except requests.exceptions.RequestException as e:
            logger.error("Network error during download: %s", e)
            if self._cancelled:
                self.error.emit("Download cancelled by user.")
            else:
                self.error.emit(f"Network error: {e}")
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("Unexpected error during download: %s", e)
            if self._cancelled:
                self.error.emit("Download cancelled by user.")
            else:
                self.error.emit(str(e))

    def stop(self) -> None:
        """Stops the download worker and closes all network connections."""
        self._cancelled = True
        self._is_running = False
        if self._response:
            self._response.close()
        self._session.close()

    @staticmethod
    def _sanitize_folder_name(name: str) -> str:
        """Remove characters that can break shell scripts from a folder name."""
        return sanitize_name(name)

    def _rename_extracted_folder(self) -> str | None:
        """Detect a single extracted root folder with a quote in its name and rename it.

        After extraction the target directory may contain exactly one subfolder
        (the ZIP's root folder).  If that folder's name contains a single quote
        it is renamed to the sanitized version and ``self.target_dir`` is updated
        to point at the new location.

        Returns:
            The new target directory path if a rename happened, otherwise ``None``.
        """
        try:
            if not os.path.isdir(self.target_dir):
                return None

            entries = os.listdir(self.target_dir)
            # Only rename when there is exactly one subfolder (typical ZIP root)
            folders = [e for e in entries if os.path.isdir(os.path.join(self.target_dir, e))]
            if len(folders) != 1:
                return None

            old_folder = folders[0]
            if "'" not in old_folder:
                return None

            new_folder = self._sanitize_folder_name(old_folder)
            if not new_folder:
                return None

            old_path = os.path.join(self.target_dir, old_folder)
            new_path = os.path.join(self.target_dir, new_folder)

            # Avoid collisions: if the sanitized name already exists, keep original
            if os.path.exists(new_path):
                logger.warning(
                    "Sanitized folder name '%s' already exists — keeping original '%s'.",
                    new_folder, old_folder,
                )
                return None

            os.rename(old_path, new_path)
            self.target_dir = new_path
            logger.info(
                "Renamed extracted folder to remove quote: '%s' → '%s' (target_dir=%s)",
                old_folder, new_folder, self.target_dir,
            )
            return self.target_dir

        except OSError as exc:
            logger.error("Failed to rename extracted folder: %s", exc)
            return None


class ProcessMonitorWorker(QThread):
    """Monitors a process by its PID and emits when it's finished."""

    finished = pyqtSignal()

    def __init__(self, pid: int, parent: QObject | None = None) -> None:
        """Initialize a worker that monitors a process by PID.

        Args:
            pid: The process ID to monitor.
            parent: Parent QObject.
        """
        super().__init__(parent)
        self.pid = pid
        self._running = True

    def run(self) -> None:
        """Poll the PID using os.kill() until the process exits or stop() is called."""
        if not self.pid > 0:
            logger.warning("ProcessMonitor: Invalid PID (%s), stopping.", self.pid)
            return

        logger.info("ProcessMonitor: Monitoring PID %s", self.pid)
        while self._running:
            try:
                os.kill(self.pid, 0)
            except OSError:
                logger.info("ProcessMonitor: PID %s finished.", self.pid)
                self._running = False
                break
            else:
                if not self._running:
                    break
                self.msleep(1000)

        logger.info("ProcessMonitor: Stopping monitor for %s", self.pid)
        self.finished.emit()

    def stop(self) -> None:
        """Stops the process monitor thread."""
        self._running = False


class UpdateCheckWorker(QThread):
    """Fetches the latest GitHub release off the GUI thread."""

    finished = pyqtSignal(object, str)  # (release dict or None, error message)

    def run(self) -> None:
        try:
            release = check_latest_release()
            self.finished.emit(release, "")
        except requests.exceptions.RequestException as e:
            logger.error("Update check failed: %s", e)
            self.finished.emit(None, str(e))


class UpdateDownloadWorker(QThread):
    """Downloads a release asset to a local file with progress signals."""

    progress = pyqtSignal(int)
    bytes_received = pyqtSignal("long long", "long long")
    finished = pyqtSignal(str)  # local file path
    error = pyqtSignal(str)

    def __init__(self, url: str, target_path: str, bandwidth_limit: int = 0) -> None:
        """Initialize a plain file download worker.

        Args:
            url: URL to download from.
            target_path: Local file path to write to.
            bandwidth_limit: Max download speed in bytes/sec. 0 means unlimited.
        """
        super().__init__()
        self.url = url
        self.target_path = target_path
        self.bandwidth_limit = bandwidth_limit
        self._cancelled = False
        self._response = None

    def run(self) -> None:
        try:
            self._response = requests.get(self.url, stream=True, timeout=30)
            self._response.raise_for_status()

            total = int(self._response.headers.get("content-length", 0))
            received = 0
            last_signal_time = 0.0
            chunk_start = time.monotonic()
            chunk_bytes = 0

            with open(self.target_path, "wb") as f:
                for chunk in self._response.iter_content(DOWNLOAD_CHUNK_SIZE):
                    if self._cancelled:
                        self._cleanup_partial()
                        self.error.emit("Download cancelled by user.")
                        return
                    f.write(chunk)
                    received += len(chunk)
                    chunk_bytes += len(chunk)

                    now = time.monotonic()
                    if now - last_signal_time >= PROGRESS_SIGNAL_INTERVAL:
                        self.bytes_received.emit(received, total)
                        if total > 0:
                            self.progress.emit(min(int(received / total * 100), 99))
                        last_signal_time = now

                    if self.bandwidth_limit > 0:
                        elapsed = time.monotonic() - chunk_start
                        min_elapsed = chunk_bytes / self.bandwidth_limit
                        if min_elapsed > elapsed:
                            time.sleep(min_elapsed - elapsed)
                        chunk_start = time.monotonic()
                        chunk_bytes = 0

            self.progress.emit(100)
            self.finished.emit(self.target_path)

        except requests.exceptions.RequestException as e:
            logger.error("Network error during update download: %s", e)
            self._cleanup_partial()
            if self._cancelled:
                self.error.emit("Download cancelled by user.")
            else:
                self.error.emit(f"Network error: {e}")
        except OSError as e:
            logger.error("Error writing update file: %s", e)
            self._cleanup_partial()
            self.error.emit(str(e))

    def stop(self) -> None:
        """Stops the download and removes the partial file."""
        self._cancelled = True
        if self._response:
            self._response.close()

    def _cleanup_partial(self) -> None:
        try:
            if os.path.exists(self.target_path):
                os.remove(self.target_path)
        except OSError:
            pass


class FlatpakInstallWorker(QThread):
    """Runs the flatpak install command off the GUI thread."""

    finished = pyqtSignal(bool, str)  # (success, output)

    def __init__(self, flatpak_path: str) -> None:
        """Initialize the installer.

        Args:
            flatpak_path: Path to the downloaded .flatpak bundle.
        """
        super().__init__()
        self.flatpak_path = flatpak_path

    def run(self) -> None:
        success, output = install_flatpak(self.flatpak_path)
        self.finished.emit(success, output)
