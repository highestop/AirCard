"""Normalize uploaded artwork using macOS ImageIO through the built-in sips tool.

All callers receive the same orientation-corrected, centered 1536 × 969 PNG.
No third-party Python image package is needed.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import tempfile
from pathlib import Path

WIDTH = 1536
HEIGHT = 969
MAX_INPUT_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 48_000_000
MAX_SIDE = 16_384
SIPS = "/usr/bin/sips"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# Unlike `sips -g orientation`, the ImageIO properties expose EXIF orientation.
_INSPECT_SCRIPT = """
if (sips.images.length === 1) {
    var image = sips.images[0];
    print(JSON.stringify({width: image.size.width, height: image.size.height,
        orientation: image.properties.Orientation || 1}));
}
"""


def _run(*arguments: str) -> str:
    try:
        result = subprocess.run(
            [SIPS, *arguments], check=True, capture_output=True,
            text=True, timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Could not decode or process this image with macOS sips.") from error
    return result.stdout


def _inspect_image(path: Path, directory: Path) -> tuple[int, int, int]:
    script = directory / "inspect.js"
    script.write_text(_INSPECT_SCRIPT, encoding="utf-8")
    output = _run(str(path), "--js", str(script))
    for line in reversed(output.splitlines()):
        try:
            info = json.loads(line)
            width, height = int(info["width"]), int(info["height"])
            orientation = int(info.get("orientation", 1))
        except (ValueError, TypeError, KeyError):
            continue
        if width > 0 and height > 0:
            return width, height, orientation
    raise ValueError("This file is not an image supported by macOS.")


def _clean_png(data: bytes, expected_size: tuple[int, int] | None = None) -> bytes:
    """Remove inherited EXIF orientation and text from a newly encoded PNG.

    sips keeps the original EXIF orientation after rotating the pixels. Removing
    it prevents browser previews (and subsequent conversions) rotating twice.
    ICC color profiles remain in the file.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Image processing did not produce a PNG.")
    chunks = [PNG_SIGNATURE]
    offset = len(PNG_SIGNATURE)
    size = None
    while offset + 12 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + length
        if end > len(data):
            break
        if kind == b"IHDR" and length == 13:
            size = struct.unpack_from(">II", data, offset + 8)
        if kind not in (b"eXIf", b"tEXt", b"zTXt", b"iTXt"):
            chunks.append(data[offset:end])
        offset = end
        if kind == b"IEND":
            if size and (expected_size is None or size == expected_size):
                return b"".join(chunks)
            break
    raise ValueError("Image processing produced an invalid PNG or image size.")


def prepare_image(data: bytes) -> bytes:
    """Decode artwork, center-crop to the Wallet ratio, and return a PNG.

    Accepts every raster format the host's sips/ImageIO decoder can open,
    regardless of its filename. Rejects empty/corrupt files, uploads over
    30 MiB, decoded images over 48 million pixels, or sides over 16,384 pixels.
    Raises ValueError with a displayable error for all conversion failures.
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError("Choose a non-empty image file.")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("Image files must be at most 30 MiB.")

    try:
        with tempfile.TemporaryDirectory(prefix="apple-wallet-card-skinner-image-") as temporary:
            directory = Path(temporary)
            source = directory / "upload"
            working = directory / "normalized.png"
            source.write_bytes(data)
            width, height, orientation = _inspect_image(source, directory)
            if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
                raise ValueError("Images must be at most 48 megapixels and 16,384 pixels per side.")

            # EXIF orientation values map raw sensor pixels into display order.
            # Apply each transform separately after clearing inherited orientation.
            transformations = {
                2: ["--flip", "horizontal"],
                3: ["--rotate", "180"],
                4: ["--flip", "vertical"],
                5: ["--rotate", "90", "--flip", "horizontal"],
                6: ["--rotate", "90"],
                7: ["--rotate", "270", "--flip", "horizontal"],
                8: ["--rotate", "270"],
            }
            _run("--setProperty", "format", "png", str(source), "--out", str(working))
            working.write_bytes(_clean_png(working.read_bytes()))
            operations = transformations.get(orientation, [])
            for index in range(0, len(operations), 2):
                _run(*operations[index:index + 2], str(working), "--out", str(working))
            if orientation in (5, 6, 7, 8):
                width, height = height, width

            scale = max(WIDTH / width, HEIGHT / height)
            resized_width, resized_height = math.ceil(width * scale), math.ceil(height * scale)
            # Very thin images could otherwise expand into enormous intermediates.
            # Crop the unused edges first in that case, keeping the same center.
            if resized_width * resized_height > MAX_PIXELS:
                crop_width = min(width, max(1, math.ceil(height * WIDTH / HEIGHT)))
                crop_height = min(height, max(1, math.ceil(width * HEIGHT / WIDTH)))
                _run("--cropToHeightWidth", str(crop_height), str(crop_width),
                     str(working), "--out", str(working))
                width, height = crop_width, crop_height
                scale = max(WIDTH / width, HEIGHT / height)
                resized_width, resized_height = math.ceil(width * scale), math.ceil(height * scale)

            _run("--resampleHeightWidth", str(resized_height), str(resized_width),
                 str(working), "--out", str(working))
            _run("--cropToHeightWidth", str(HEIGHT), str(WIDTH),
                 str(working), "--out", str(working))
            return _clean_png(working.read_bytes(), (WIDTH, HEIGHT))
    except OSError as error:
        raise ValueError("Could not read or prepare the image file.") from error
