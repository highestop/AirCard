"""Find complete card faces in the Mac cache without assigning write artwork.

Callers must independently require a card verified on the selected device.
Cache availability never establishes phone inventory or write authorization.
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile

from .card_assets import PNG_ASSET_NAMES
from .image_processing import MAX_INPUT_BYTES, MAX_PIXELS, MAX_SIDE, PNG_SIGNATURE, SIPS
from .wallet_discovery import valid_card_id

# FrontFace/Preview are full rendered faces when stored in a supported format.
# PlaceHolder, icons, logos and strips are not complete card artwork.
_CANDIDATES = tuple((suffix, leaf) for suffix in (".cache", ".pkcache")
                    for leaf in ("FrontFace", "Preview")) + tuple(
    (".pkpass", leaf) for leaf in PNG_ASSET_NAMES)


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    if (len(data) >= 33 and data.startswith(PNG_SIGNATURE)
            and data[8:16] == b"\x00\x00\x00\x0dIHDR"):
        return struct.unpack_from(">II", data, 16)
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return None
    # Locate a JPEG start-of-frame segment before entropy-coded pixel data.
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return None
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker in (0xDA, 0xD9):
            return None
        if marker in (0x01, *range(0xD0, 0xD8)):
            continue
        if offset + 2 > len(data):
            return None
        length = struct.unpack_from(">H", data, offset)[0]
        if length < 2 or offset + length > len(data):
            return None
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if length < 8:
                return None
            height, width = struct.unpack_from(">HH", data, offset + 3)
            return width, height
        offset += length
    return None


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _decode_supported_image(data: bytes) -> bool:
    # Decode a bounded copy, never modify the Mac cache. This also rejects
    # corrupt files whose headers still advertise plausible image dimensions.
    with tempfile.TemporaryDirectory(prefix="wallet-preview-check-") as temporary:
        source = Path(temporary) / "source"
        output = Path(temporary) / "decoded.png"
        source.write_bytes(data)
        completed = subprocess.run([SIPS, "-s", "format", "png", str(source), "--out", str(output)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=10, check=False)
        if completed.returncode != 0 or not output.is_file():
            return False
        with output.open("rb") as stream:
            return stream.read(8) == PNG_SIGNATURE


@lru_cache(maxsize=64)
def _valid_image(path: Path, stamp: tuple) -> bool:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or _stamp(info) != stamp or not 0 < info.st_size <= MAX_INPUT_BYTES:
                return False
            data = stream.read(MAX_INPUT_BYTES + 1)
            if len(data) > MAX_INPUT_BYTES or _stamp(os.fstat(stream.fileno())) != stamp:
                return False
        size = _image_dimensions(data)
        if size is None or min(size) <= 0 or max(size) > MAX_SIDE or size[0] * size[1] > MAX_PIXELS:
            return False
        return _decode_supported_image(data)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def find_cached_artwork(root: Path, card_id: str) -> Path | None:
    """Return a bounded, decodable full-face file for this exact card ID only."""
    if not valid_card_id(card_id):
        return None
    root = Path(root)
    cards = root / "Cards"
    try:
        if root.is_symlink() or cards.is_symlink() or not cards.is_dir():
            return None
        resolved_root = root.resolve(strict=True)
        for suffix, leaf in _CANDIDATES:
            directory = cards / (card_id + suffix)
            candidate = directory / leaf
            if directory.is_symlink() or candidate.is_symlink():
                continue
            try:
                info = candidate.lstat()
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_INPUT_BYTES:
                    continue
                candidate.resolve(strict=True).relative_to(resolved_root)
                if _valid_image(candidate, _stamp(info)):
                    return candidate
            except (OSError, ValueError):
                continue
    except (OSError, ValueError):
        return None
    return None
