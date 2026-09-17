"""Tests for extracting the embedded icon out of a Windows executable."""

from __future__ import annotations

import struct

import pytest

from PyQt6.QtCore import QBuffer, QByteArray
from PyQt6.QtGui import QColor, QImage


def _png_bytes(size: int, color: str) -> bytes:
    """Render a solid square as PNG — the modern in-.ico image format."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.setData(QByteArray())
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(buffer.data())


def _resource_dir(entries: list[tuple[int, int, bool]]) -> bytes:
    """Build a resource directory table with only ID entries."""
    out = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries))
    for entry_id, offset, is_dir in entries:
        out += struct.pack("<II", entry_id, offset | (0x80000000 if is_dir else 0))
    return out


def _build_exe(images: list[bytes], magic: int = 0x10B) -> bytes:
    """Assemble a minimal PE whose .rsrc section holds one icon group."""
    rsrc_rva = 0x1000
    header_size = 16 + 8  # directory header + one ID entry

    # Lay the resource tree out first so every offset is known up front.
    icon_type = 0x20
    icon_ids = icon_type + 16 + 8 * len(images)
    icon_langs = icon_ids + header_size * len(images)
    icon_data_entries = icon_langs + header_size * len(images)
    group_type = icon_data_entries + 16 * len(images)
    group_id = group_type + header_size
    group_lang = group_id + header_size
    group_data_entry = group_lang + header_size
    images_at = group_data_entry + 16

    group = struct.pack("<HHH", 0, 1, len(images))
    offset = images_at
    image_blobs = b""
    for i, image in enumerate(images):
        group += struct.pack("<BBBBHHIH", 0, 0, 0, 0, 1, 32, len(image), i + 1)
        image_blobs += image
        offset += len(image)
    group_at = offset

    rsrc = _resource_dir([(3, icon_type, True), (14, group_type, True)])
    rsrc += _resource_dir([(i + 1, icon_ids + header_size * i, True) for i in range(len(images))])
    for i in range(len(images)):
        rsrc += _resource_dir([(1033, icon_langs + header_size * i, True)])
    for i in range(len(images)):
        rsrc += _resource_dir([(0, icon_data_entries + 16 * i, False)])
    image_rva = rsrc_rva + images_at
    for image in images:
        rsrc += struct.pack("<IIII", image_rva, len(image), 0, 0)
        image_rva += len(image)
    rsrc += _resource_dir([(1, group_id, True)])
    rsrc += _resource_dir([(1033, group_lang, True)])
    rsrc += _resource_dir([(0, group_data_entry, False)])
    rsrc += struct.pack("<IIII", rsrc_rva + group_at, len(group), 0, 0)
    rsrc += image_blobs + group

    optional_size = 224 if magic == 0x10B else 240
    data_dir_at = 96 if magic == 0x10B else 112

    optional = struct.pack("<H", magic) + b"\0" * (data_dir_at - 2)
    optional += struct.pack("<II", rsrc_rva, len(rsrc))          # data dir 0 (export)
    optional += struct.pack("<II", 0, 0)                          # data dir 1 (import)
    optional += struct.pack("<II", rsrc_rva, len(rsrc))           # data dir 2 (resource)
    optional = optional.ljust(optional_size, b"\0")

    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, optional_size, 0)
    section = (b".rsrc\0\0\0" + struct.pack("<IIII", len(rsrc), rsrc_rva, len(rsrc), 0x400)
               + b"\0" * 16)

    pe_at = 0x40
    dos = b"MZ" + b"\0" * 0x3A + struct.pack("<I", pe_at)
    headers = dos + b"PE\0\0" + coff + optional + section
    return headers.ljust(0x400, b"\0") + rsrc


class TestExtractExeIcon:
    @pytest.mark.parametrize("magic,label", [(0x10B, "PE32"), (0x20B, "PE32+")])
    def test_extracts_the_icon(self, tmp_path, magic, label):
        from gameyfin_frontend.services.exe_icon import extract_exe_icon

        exe = tmp_path / "game.exe"
        exe.write_bytes(_build_exe([_png_bytes(64, "#ff0000")], magic=magic))
        dest = tmp_path / "icons" / "256x256" / "apps" / "game.png"

        assert extract_exe_icon(str(exe), str(dest)) == str(dest)
        written = QImage(str(dest))
        assert (written.width(), written.height()) == (64, 64), label

    def test_prefers_the_largest_frame(self, tmp_path):
        """Executables list their 16x16 frame first; the big one is the useful one."""
        from gameyfin_frontend.services.exe_icon import extract_exe_icon

        exe = tmp_path / "game.exe"
        exe.write_bytes(_build_exe([_png_bytes(16, "#ff0000"), _png_bytes(128, "#00ff00")]))
        dest = tmp_path / "game.png"

        assert extract_exe_icon(str(exe), str(dest))
        assert QImage(str(dest)).width() == 128

    def test_scales_oversized_icons_down(self, tmp_path):
        from gameyfin_frontend.services.exe_icon import extract_exe_icon

        exe = tmp_path / "game.exe"
        exe.write_bytes(_build_exe([_png_bytes(512, "#0000ff")]))
        dest = tmp_path / "game.png"

        assert extract_exe_icon(str(exe), str(dest), size=256)
        assert QImage(str(dest)).width() == 256

    def test_returns_none_without_an_icon(self, tmp_path):
        """A file that isn't a PE, or carries no icon, must not raise."""
        from gameyfin_frontend.services.exe_icon import extract_exe_icon

        exe = tmp_path / "not-really.exe"
        exe.write_bytes(b"#!/bin/sh\necho hello\n")

        assert extract_exe_icon(str(exe), str(tmp_path / "out.png")) is None
        assert not (tmp_path / "out.png").exists()

    def test_returns_none_for_a_truncated_executable(self, tmp_path):
        from gameyfin_frontend.services.exe_icon import extract_exe_icon

        exe = tmp_path / "cut.exe"
        exe.write_bytes(_build_exe([_png_bytes(32, "#ffffff")])[:0x80])

        assert extract_exe_icon(str(exe), str(tmp_path / "out.png")) is None
