"""Exercise the same native decoder used for browser uploads and stored images."""
import binascii
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from image_processing import HEIGHT, MAX_INPUT_BYTES, WIDTH, prepare_image


def png_image(width, height, color):
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", binascii.crc32(kind + content) & 0xffffffff))
    rows = b"".join(b"\0" + b"".join(bytes(color(x, y)) for x in range(width))
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def bmp_pixels(png):
    """Use an independent output format to read actual normalized pixel colors."""
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "result.png"
        destination = Path(temporary) / "result.bmp"
        source.write_bytes(png)
        subprocess.run(["/usr/bin/sips", "-s", "format", "bmp", str(source),
                        "--out", str(destination)], check=True, capture_output=True)
        data = destination.read_bytes()
    offset = struct.unpack_from("<I", data, 10)[0]
    width, signed_height = struct.unpack_from("<ii", data, 18)
    depth = struct.unpack_from("<H", data, 28)[0]
    height = abs(signed_height)
    stride = ((width * depth + 31) // 32) * 4
    def pixel(x, y):
        row = height - 1 - y if signed_height > 0 else y
        index = offset + row * stride + x * (depth // 8)
        return tuple(reversed(data[index:index + 3]))
    return width, height, pixel


@unittest.skipUnless(Path("/usr/bin/sips").is_file(), "Requires the macOS native image decoder")
class ImageProcessingTests(unittest.TestCase):
    def test_landscape_is_center_cropped_without_squeezing(self):
        source = png_image(300, 100, lambda x, y:
                           (255, 0, 0) if x < 100 else
                           (0, 255, 0) if x < 200 else (0, 0, 255))
        result = prepare_image(source)
        width, height, pixel = bmp_pixels(result)
        self.assertEqual((width, height), (WIDTH, HEIGHT))
        self.assertEqual(pixel(int(width * .2), height // 2), (0, 255, 0))
        self.assertEqual(pixel(int(width * .8), height // 2), (0, 255, 0))
        self.assertEqual(pixel(int(width * .05), height // 2), (255, 0, 0))
        self.assertEqual(pixel(int(width * .95), height // 2), (0, 0, 255))

    def test_portrait_is_cropped_to_the_center(self):
        source = png_image(100, 300, lambda x, y:
                           (255, 0, 0) if y < 100 else
                           (0, 255, 0) if y < 200 else (0, 0, 255))
        width, height, pixel = bmp_pixels(prepare_image(source))
        self.assertEqual((width, height), (WIDTH, HEIGHT))
        self.assertEqual(pixel(width // 2, 0), (0, 255, 0))
        self.assertEqual(pixel(width // 2, height - 1), (0, 255, 0))

    def test_phone_photo_orientation_is_applied_once(self):
        colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
        source = png_image(100, 200, lambda x, y: colors[(2 if y >= 100 else 0) + (x >= 50)])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.png"
            jpeg = Path(temporary) / "source.jpg"
            path.write_bytes(source)
            subprocess.run(["/usr/bin/sips", "-s", "format", "jpeg", str(path),
                            "--out", str(jpeg)], check=True, capture_output=True)
            original = jpeg.read_bytes()
        expected_quadrants = {
            1: (0, 1, 2, 3), 2: (1, 0, 3, 2), 3: (3, 2, 1, 0),
            4: (2, 3, 0, 1), 5: (0, 2, 1, 3), 6: (2, 0, 3, 1),
            7: (3, 1, 2, 0), 8: (1, 3, 0, 2),
        }
        for orientation, expected in expected_quadrants.items():
            with self.subTest(orientation=orientation):
                # Minimal EXIF IFD describing each camera display orientation.
                exif = (b"Exif\0\0II" + struct.pack("<HIH", 42, 8, 1)
                        + struct.pack("<HHIHHI", 274, 3, 1, orientation, 0, 0))
                data = (original[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2)
                        + exif + original[2:])
                width, height, pixel = bmp_pixels(prepare_image(data))
                self.assertEqual((width, height), (WIDTH, HEIGHT))
                points = ((width // 4, height // 4), (3 * width // 4, height // 4),
                          (width // 4, 3 * height // 4), (3 * width // 4, 3 * height // 4))
                for point, color_index in zip(points, expected):
                    observed = pixel(*point)
                    self.assertTrue(all(abs(a - b) < 15 for a, b in zip(observed, colors[color_index])),
                                    (observed, colors[color_index]))

    def test_extremely_thin_image_keeps_intermediate_bounded(self):
        data = prepare_image(png_image(1, 16_000, lambda x, y: (0, 255, 0)))
        self.assertEqual(struct.unpack_from(">II", data, 16), (WIDTH, HEIGHT))

    def test_corrupt_or_empty_upload_is_rejected(self):
        for data in (b"", b"not an image", b"\x89PNG\r\n\x1a\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                prepare_image(data)

    def test_upload_size_is_checked_before_decoding(self):
        with patch("image_processing._inspect_image") as inspect:
            with self.assertRaisesRegex(ValueError, "30 MiB"):
                prepare_image(b"x" * (MAX_INPUT_BYTES + 1))
        inspect.assert_not_called()

    def test_pixel_and_side_limits_are_checked_before_conversion(self):
        for size in ((7000, 7000, 1), (16_385, 1, 1)):
            with self.subTest(size=size), patch("image_processing._inspect_image", return_value=size):
                with self.assertRaisesRegex(ValueError, "48 megapixels"):
                    prepare_image(b"image")


if __name__ == "__main__":
    unittest.main()
