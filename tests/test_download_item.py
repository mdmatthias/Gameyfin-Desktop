import pytest

from gameyfin_frontend.utils import format_size, parse_size


class TestFormatSize:
    def test_bytes(self):
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"
        assert format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.00 KB"
        assert format_size(1536) == "1.50 KB"
        assert format_size(1048575) == "1024.00 KB"

    def test_megabytes(self):
        assert format_size(1048576) == "1.00 MB"
        assert format_size(5242880) == "5.00 MB"
        assert format_size(1073741823) == "1024.00 MB"

    def test_gigabytes(self):
        assert format_size(1073741824) == "1.00 GB"
        assert format_size(3221225472) == "3.00 GB"

    def test_terabytes(self):
        assert format_size(1099511627776) == "1.00 TB"

    def test_precision(self):
        result = format_size(1234567890)
        # Should have exactly 2 decimal places
        mb_part = result.split(" ")[0]
        assert "." in mb_part
        assert len(mb_part.split(".")[1]) == 2


class TestParseSize:
    def test_bytes(self):
        assert parse_size("512 B") == 512

    def test_kilobytes(self):
        assert parse_size("1 KiB") == 1024
        assert parse_size("1 KB") == 1000

    def test_megabytes(self):
        assert parse_size("1 MiB") == 1024 ** 2
        assert parse_size("1 MB") == 1000 ** 2

    def test_gigabytes(self):
        assert parse_size("1 GiB") == 1024 ** 3
        assert parse_size("1 GB") == 1000 ** 3

    def test_comma_decimal(self):
        assert parse_size("1,5 MiB") == int(1.5 * 1024 ** 2)

    def test_invalid(self):
        assert parse_size("not a size") == 0
        assert parse_size("") == 0
