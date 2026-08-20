"""Disk-backed, threaded artwork cache for the native library UI.

Covers, headers and screenshots are served by the Gameyfin server with a
seven-day cache header and an ETag; caching them on disk keeps the grid instant
across restarts. Fetches run on a bounded thread pool so scrolling a large
library never blocks the GUI thread, and identical in-flight requests are
coalesced.

A ``concurrent.futures`` pool is used rather than ``QThreadPool``: the latter's
C++ destructor waits for its threads while the calling thread still holds the
GIL, which deadlocks as soon as a worker needs the GIL to finish up.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, pyqtSignal

from ..config import IMAGE_CACHE_DIR, IMAGE_FETCH_THREADS
from ..settings import SettingsManager
from .gameyfin_api import GameImage, GameyfinApiClient, GameyfinApiError

logger = logging.getLogger(__name__)


class ImageCache(QObject):
    """Serves artwork bytes from disk, fetching missing entries in the background.

    Emits ``ready(image_id, data)`` on success and ``failed(image_id, message)``
    when an image cannot be fetched. Both are emitted from worker threads, so Qt
    delivers them to consumers on the GUI thread, where the bytes can safely be
    turned into a QPixmap.
    """

    ready = pyqtSignal(int, bytes)
    failed = pyqtSignal(int, str)

    def __init__(self, client: GameyfinApiClient, settings: SettingsManager,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.settings = settings
        self._pool = ThreadPoolExecutor(
            max_workers=IMAGE_FETCH_THREADS, thread_name_prefix="gf-image"
        )
        self._pending: set[int] = set()
        self._lock = threading.Lock()

    @property
    def cache_dir(self) -> str:
        """Return the directory holding cached artwork, creating it on demand."""
        path = os.path.join(self.settings.get_config_dir(), IMAGE_CACHE_DIR)
        os.makedirs(path, exist_ok=True)
        return path

    def cache_path(self, image: GameImage) -> str:
        """Return the on-disk path for *image*."""
        return os.path.join(self.cache_dir, f"{image.type.lower()}_{image.id}")

    def cached_bytes(self, image: GameImage) -> bytes | None:
        """Return the cached bytes of *image*, or None when not cached."""
        path = self.cache_path(image)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "rb") as f:
                return f.read() or None
        except OSError as e:
            logger.debug("Could not read cached image %s: %s", image.id, e)
            return None

    def request(self, image: GameImage) -> bytes | None:
        """Return cached bytes for *image*, or schedule a fetch and return None.

        When a fetch is scheduled, ``ready`` or ``failed`` fires later with the
        same image id.
        """
        data = self.cached_bytes(image)
        if data is not None:
            return data

        with self._lock:
            if image.id in self._pending:
                return None
            self._pending.add(image.id)

        cache_path = self.cache_path(image)
        try:
            self._pool.submit(self._fetch, image, cache_path)
        except RuntimeError as e:
            # Pool already shut down (app is quitting)
            logger.debug("Not fetching image %s: %s", image.id, e)
            with self._lock:
                self._pending.discard(image.id)
        return None

    def clear(self) -> None:
        """Delete every cached artwork file."""
        directory = self.cache_dir
        for name in os.listdir(directory):
            try:
                os.remove(os.path.join(directory, name))
            except OSError as e:
                logger.debug("Could not remove cached image %s: %s", name, e)

    def shutdown(self) -> None:
        """Drop queued fetches and stop accepting new ones (called on app quit)."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _fetch(self, image: GameImage, cache_path: str) -> None:
        """Download *image* on a worker thread, cache it and emit the result."""
        try:
            data = self.client.fetch_image(image)
        except GameyfinApiError as e:
            self._finish(image.id)
            logger.debug("Image %s fetch failed: %s", image.id, e)
            self.failed.emit(image.id, str(e))
            return

        try:
            with open(cache_path, "wb") as f:
                f.write(data)
        except OSError as e:
            logger.debug("Could not cache image %s: %s", image.id, e)

        self._finish(image.id)
        self.ready.emit(image.id, data)

    def _finish(self, image_id: int) -> None:
        """Drop *image_id* from the in-flight set."""
        with self._lock:
            self._pending.discard(image_id)
