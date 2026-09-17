"""Extract the embedded application icon from a Windows PE executable.

Proton writes icons alongside the .desktop files it captures during install,
but an executable picked by hand has none — so the icon is read straight out
of the .exe's resource section (RT_GROUP_ICON / RT_ICON), reassembled into an
ICO image and decoded by Qt, which needs no extra dependency.
"""

from __future__ import annotations

import logging
import os
import struct

from PyQt6.QtCore import QBuffer, QByteArray, Qt
from PyQt6.QtGui import QImage, QImageReader

logger = logging.getLogger(__name__)

_RT_ICON = 3
_RT_GROUP_ICON = 14

_ICON_DIR_HEADER = struct.Struct("<HHH")          # reserved, type, count
_GROUP_ICON_ENTRY = struct.Struct("<BBBBHHIH")    # ... bytes_in_res, icon id
_ICON_DIR_ENTRY = struct.Struct("<BBBBHHII")      # ... bytes_in_res, offset


class _PEResources:
    """Minimal PE reader exposing the executable's icon resources."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._sections: list[tuple[int, int, int, int]] = []
        self._resource_rva = 0
        self._parse_headers()

    def _parse_headers(self) -> None:
        data = self._data
        if data[:2] != b"MZ":
            raise ValueError("not a DOS/PE executable")

        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset:pe_offset + 4] != b"PE\0\0":
            raise ValueError("missing PE signature")

        coff = pe_offset + 4
        num_sections, = struct.unpack_from("<H", data, coff + 2)
        optional_size, = struct.unpack_from("<H", data, coff + 16)
        optional = coff + 20

        magic, = struct.unpack_from("<H", data, optional)
        if magic == 0x20B:        # PE32+
            data_dirs = optional + 112
        elif magic == 0x10B:      # PE32
            data_dirs = optional + 96
        else:
            raise ValueError(f"unknown optional header magic 0x{magic:x}")

        # Data directory entry 2 is the resource table.
        self._resource_rva, _size = struct.unpack_from("<II", data, data_dirs + 2 * 8)
        if not self._resource_rva:
            raise ValueError("executable has no resource section")

        sections = optional + optional_size
        for i in range(num_sections):
            header = sections + i * 40
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, header + 8)
            self._sections.append((virtual_address, max(virtual_size, raw_size), raw_size, raw_offset))

    def _offset_for_rva(self, rva: int) -> int:
        for virtual_address, virtual_size, _raw_size, raw_offset in self._sections:
            if virtual_address <= rva < virtual_address + virtual_size:
                return raw_offset + (rva - virtual_address)
        raise ValueError(f"RVA 0x{rva:x} is outside every section")

    def _entries(self, directory_offset: int) -> list[tuple[int, int, bool]]:
        """Return (id, offset, is_directory) for one resource directory's entries."""
        base = self._offset_for_rva(self._resource_rva)
        named, ids = struct.unpack_from("<HH", self._data, directory_offset + 12)
        result = []
        for i in range(named + ids):
            entry = directory_offset + 16 + i * 8
            name, offset = struct.unpack_from("<II", self._data, entry)
            is_dir = bool(offset & 0x80000000)
            result.append((name & 0x7FFFFFFF, base + (offset & 0x7FFFFFFF), is_dir))
        return result

    def _leaf_data(self, directory_offset: int) -> bytes:
        """Follow a resource subtree down to its first data leaf."""
        entries = self._entries(directory_offset)
        if not entries:
            raise ValueError("empty resource directory")
        _id, offset, is_dir = entries[0]
        if is_dir:
            return self._leaf_data(offset)
        data_rva, size = struct.unpack_from("<II", self._data, offset)
        start = self._offset_for_rva(data_rva)
        return self._data[start:start + size]

    def icon_resources(self) -> tuple[bytes, dict[int, bytes]]:
        """Return the first icon group and every RT_ICON image, keyed by id."""
        root = self._offset_for_rva(self._resource_rva)
        group = b""
        icons: dict[int, bytes] = {}

        for type_id, type_offset, is_dir in self._entries(root):
            if not is_dir:
                continue
            if type_id == _RT_GROUP_ICON and not group:
                group = self._leaf_data(type_offset)
            elif type_id == _RT_ICON:
                for icon_id, icon_offset, icon_is_dir in self._entries(type_offset):
                    if icon_is_dir:
                        icons[icon_id] = self._leaf_data(icon_offset)

        if not group or not icons:
            raise ValueError("executable contains no icon group")
        return group, icons


def _build_ico(group: bytes, icons: dict[int, bytes]) -> bytes:
    """Turn a GRPICONDIR plus its images back into a standalone .ico file."""
    reserved, image_type, count = _ICON_DIR_HEADER.unpack_from(group, 0)
    entries = []
    images = []
    offset = _ICON_DIR_HEADER.size + count * _ICON_DIR_ENTRY.size

    for i in range(count):
        width, height, colors, entry_reserved, planes, bits, size, icon_id = \
            _GROUP_ICON_ENTRY.unpack_from(group, _ICON_DIR_HEADER.size + i * _GROUP_ICON_ENTRY.size)
        image = icons.get(icon_id)
        if image is None:
            continue
        entries.append(_ICON_DIR_ENTRY.pack(width, height, colors, entry_reserved,
                                            planes, bits, len(image), offset))
        images.append(image)
        offset += len(image)

    if not entries:
        raise ValueError("icon group references no known images")

    header = _ICON_DIR_HEADER.pack(reserved, image_type, len(entries))
    return header + b"".join(entries) + b"".join(images)


def _largest_image(ico: bytes) -> QImage | None:
    """Decode an .ico and return its biggest frame.

    ``QImage.fromData`` would hand back whichever frame comes first, which in a
    typical executable is the 16x16 one.
    """
    buffer = QBuffer()
    buffer.setData(QByteArray(ico))
    buffer.open(QBuffer.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"ICO")

    best: QImage | None = None
    for index in range(max(reader.imageCount(), 1)):
        if not reader.jumpToImage(index):
            break
        image = reader.read()
        if image.isNull():
            continue
        if best is None or image.width() * image.height() > best.width() * best.height():
            best = image

    buffer.close()
    return best


def extract_exe_icon(exe_path: str, dest_path: str, size: int = 256) -> str | None:
    """Write the executable's icon to ``dest_path`` as a PNG.

    Args:
        exe_path: Path to the Windows .exe to read.
        dest_path: Path of the .png to write. Parent directories are created.
        size: Maximum edge length of the written image. Smaller icons are not
            upscaled, since blowing up a 32x32 icon only makes it blurry.

    Returns:
        ``dest_path`` on success, or ``None`` if the executable has no usable
        icon or could not be read.
    """
    try:
        with open(exe_path, "rb") as f:
            data = f.read()
        group, icons = _PEResources(data).icon_resources()
        ico = _build_ico(group, icons)
    except (OSError, ValueError, struct.error, IndexError) as e:
        logger.info("No icon extracted from %s: %s", exe_path, e)
        return None

    image = _largest_image(ico)
    if image is None:
        logger.info("Qt could not decode the icon embedded in %s", exe_path)
        return None

    if image.width() > size or image.height() > size:
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)

    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        if not image.save(dest_path, "PNG"):
            logger.warning("Failed to write icon to %s", dest_path)
            return None
    except OSError as e:
        logger.warning("Failed to write icon to %s: %s", dest_path, e)
        return None

    logger.info("Extracted icon from %s to %s", exe_path, dest_path)
    return dest_path
