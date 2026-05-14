"""Tests for background workers (StreamDownloadWorker, ProcessMonitorWorker)."""

import os
import signal
import time
from unittest.mock import MagicMock, patch

import pytest


class TestStreamDownloadWorker:
    def test_worker_initializes(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com/file.zip", "/tmp/test_dir")
        assert worker.url == "http://example.com/file.zip"
        assert worker.target_dir == "/tmp/test_dir"
        assert worker.cookies == {}
        assert worker.estimated_total == 0
        assert worker._is_running is True
        assert worker._cancelled is False

    def test_worker_initializes_with_cookies(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        cookies = {"session": "abc123"}
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir", cookies=cookies)
        assert worker.cookies == cookies

    def test_worker_initializes_with_estimated_total(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir", estimated_total=1048576)
        assert worker.estimated_total == 1048576

    def test_stop_sets_running_to_false(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir")
        worker.stop()
        assert worker._is_running is False
        assert worker._cancelled is True

    def test_stop_closes_session(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir")
        worker._response = MagicMock()
        worker.stop()
        worker._response.close.assert_called_once()
        # Session close should be called
        assert worker._session is not None

    def test_stop_with_no_response(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir")
        worker._response = None
        # Should not raise
        worker.stop()
        assert worker._is_running is False


class TestProcessMonitorWorker:
    def test_worker_initializes(self):
        from gameyfin_frontend.workers import ProcessMonitorWorker
        worker = ProcessMonitorWorker(pid=1234)
        assert worker.pid == 1234
        assert worker._running is True

    def test_stop_sets_running_to_false(self):
        from gameyfin_frontend.workers import ProcessMonitorWorker
        worker = ProcessMonitorWorker(pid=1234)
        worker.stop()
        assert worker._running is False

    def test_invalid_pid_logs_warning(self, caplog):
        from gameyfin_frontend.workers import ProcessMonitorWorker
        with caplog.at_level("WARNING"):
            worker = ProcessMonitorWorker(pid=-1)
            worker.run()
        assert "Invalid PID" in caplog.text
        assert "stopping" in caplog.text.lower()

    def test_nonexistent_pid_emits_finished(self):
        from gameyfin_frontend.workers import ProcessMonitorWorker
        finished_signals = []
        worker = ProcessMonitorWorker(pid=999999)
        worker.finished.connect(lambda: finished_signals.append(True))
        worker.run()
        assert len(finished_signals) == 1

    def test_existing_pid_runs_until_stopped(self):
        """Test with current process PID which should exist."""
        from gameyfin_frontend.workers import ProcessMonitorWorker
        finished_signals = []
        worker = ProcessMonitorWorker(pid=os.getpid())
        worker.finished.connect(lambda: finished_signals.append(True))
        # Stop immediately so we don't block
        worker.stop()
        worker.run()
        # Should have finished because we stopped it
        assert len(finished_signals) == 1

    def test_pid_zero_logs_warning(self, caplog):
        from gameyfin_frontend.workers import ProcessMonitorWorker
        with caplog.at_level("WARNING"):
            worker = ProcessMonitorWorker(pid=0)
            worker.run()
        assert "Invalid PID" in caplog.text
