import logging
import os
import time
from typing import Any

import requests
from stream_unzip import stream_unzip
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread

logger = logging.getLogger(__name__)


class StreamDownloadWorker(QObject):
    progress = pyqtSignal(int)
    current_file = pyqtSignal(str)
    bytes_received = pyqtSignal('long long', 'long long')
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, url: str, target_dir: str, cookies: dict[str, Any] | None = None, estimated_total: int = 0) -> None:
        super().__init__()
        self.url = url
        self.target_dir = target_dir
        self.cookies = cookies or {}
        self.estimated_total = estimated_total
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

            total = int(self._response.headers.get('content-length', 0)) or self.estimated_total
            received = 0
            chunk_size = 131072
            last_signal_time = 0.0

            def http_chunks():
                nonlocal received, last_signal_time
                for chunk in self._response.iter_content(chunk_size):
                    if not self._is_running:
                        return
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last_signal_time >= 0.1:
                        self.bytes_received.emit(received, total)
                        if total > 0:
                            self.progress.emit(min(int(received / total * 100), 99))
                        last_signal_time = now
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

            self.progress.emit(100)
            self.finished.emit()

        except requests.exceptions.RequestException as e:
            logger.error("Network error during download: %s", e)
            if self._cancelled:
                self.error.emit("Download cancelled by user.")
            else:
                self.error.emit(f"Network error: {e}")
        except Exception as e:
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


class ProcessMonitorWorker(QThread):
    """Monitors a process by its PID and emits when it's finished."""

    finished = pyqtSignal()

    def __init__(self, pid: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pid = pid
        self._running = True

    def run(self) -> None:
        """Poll the PID using os.kill() until the process exits or stop() is called."""
        if not self.pid > 0:
            logger.warning("ProcessMonitor: Invalid PID (%s), stopping.", self.pid)
            return

        logger.info("ProcessMonitor: Monitoring PID %s", self.pid)
        self._running = True
        while self._running:
            try:
                os.kill(self.pid, 0)
            except OSError:
                logger.info("ProcessMonitor: PID %s finished.", self.pid)
                self._running = False
                self.finished.emit()
                break
            else:
                if not self._running:
                    break
                self.msleep(1000)

        logger.info("ProcessMonitor: Stopping monitor for %s", self.pid)

    def stop(self):
        """Stops the process monitor thread."""
        self._running = False
