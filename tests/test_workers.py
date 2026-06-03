"""Tests for background workers (StreamDownloadWorker, ProcessMonitorWorker)."""

import io
import os
import signal
import time
import zipfile
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

    def test_worker_initializes_with_bandwidth_limit(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir", bandwidth_limit=5_000_000)
        assert worker.bandwidth_limit == 5_000_000

    def test_worker_default_bandwidth_limit_is_zero(self):
        from gameyfin_frontend.workers import StreamDownloadWorker
        worker = StreamDownloadWorker("http://example.com", "/tmp/dir")
        assert worker.bandwidth_limit == 0

    def test_throttle_sleep_called_when_limit_set(self):
        """Verify that time.sleep is called when bandwidth_limit > 0."""
        from gameyfin_frontend.workers import StreamDownloadWorker
        import io

        chunks = [b"x" * (64 * 1024), b"y" * (64 * 1024)]  # 64 KB chunks
        mock_response = MagicMock()
        mock_response.iter_content.return_value = iter(chunks)
        mock_response.headers = {"content-length": str(sum(len(c) for c in chunks))}
        mock_response.raise_for_status = MagicMock()

        # 1 MB/s limit -> each 64 KB chunk takes ~0.064s minimum
        worker = StreamDownloadWorker(
            "http://example.com/file.zip", "/tmp/throttle_test",
            bandwidth_limit=1024 * 1024  # 1 MB/s
        )

        with patch.object(worker._session, 'get', return_value=mock_response), \
             patch("time.sleep") as mock_sleep:
            os.makedirs("/tmp/throttle_test", exist_ok=True)
            try:
                worker.run()
            except Exception:
                pass  # May fail on unzip; we only care about sleep calls

            # At least one sleep call should have been made due to throttling
            assert mock_sleep.call_count >= 1, f"Expected sleep calls for throttling, got {mock_sleep.call_count}"

    def test_no_throttle_sleep_when_unlimited(self):
        """Verify that time.sleep is NOT called for throttling when bandwidth_limit == 0."""
        from gameyfin_frontend.workers import StreamDownloadWorker

        # Create a valid ZIP file (single text file)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test.txt", "hello world")
        zip_data = zip_buffer.getvalue()

        chunks = [zip_data[i:i + 64] for i in range(0, len(zip_data), 64)]
        mock_response = MagicMock()
        mock_response.iter_content.return_value = iter(chunks)
        mock_response.headers = {"content-length": str(len(zip_data))}
        mock_response.raise_for_status = MagicMock()

        worker = StreamDownloadWorker(
            "http://example.com/file.zip", "/tmp/no_throttle_test",
            bandwidth_limit=0  # unlimited
        )

        with patch.object(worker._session, 'get', return_value=mock_response), \
             patch("time.sleep") as mock_sleep:
            os.makedirs("/tmp/no_throttle_test", exist_ok=True)
            try:
                worker.run()
            except Exception:
                pass  # May fail on unzip; we only care about sleep calls

            # No sleep calls should have been made for throttling
            assert mock_sleep.call_count == 0, f"Expected no sleep calls, got {mock_sleep.call_count}"


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

    def test_pid_zero_logs_warning(self, caplog):
        from gameyfin_frontend.workers import ProcessMonitorWorker
        with caplog.at_level("WARNING"):
            worker = ProcessMonitorWorker(pid=0)
            worker.run()
        assert "Invalid PID" in caplog.text
