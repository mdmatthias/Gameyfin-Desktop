import pytest

from gameyfin_frontend.widgets.download_item import DownloadItemWidget


class TestFormatSize:
    def test_bytes(self):
        assert DownloadItemWidget.format_size(0) == "0 B"
        assert DownloadItemWidget.format_size(512) == "512 B"
        assert DownloadItemWidget.format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert DownloadItemWidget.format_size(1024) == "1.00 KB"
        assert DownloadItemWidget.format_size(1536) == "1.50 KB"
        assert DownloadItemWidget.format_size(1048575) == "1024.00 KB"

    def test_megabytes(self):
        assert DownloadItemWidget.format_size(1048576) == "1.00 MB"
        assert DownloadItemWidget.format_size(5242880) == "5.00 MB"
        assert DownloadItemWidget.format_size(1073741823) == "1024.00 MB"

    def test_gigabytes(self):
        assert DownloadItemWidget.format_size(1073741824) == "1.00 GB"
        assert DownloadItemWidget.format_size(3221225472) == "3.00 GB"
        assert DownloadItemWidget.format_size(1099511627776) == "1024.00 GB"

    def test_precision(self):
        result = DownloadItemWidget.format_size(1234567890)
        # Should have exactly 2 decimal places
        mb_part = result.split(" ")[0]
        assert "." in mb_part
        assert len(mb_part.split(".")[1]) == 2
