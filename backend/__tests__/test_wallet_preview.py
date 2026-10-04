"""Full-face preview lookup never treats metadata fragments as assigned art."""

from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend import wallet_preview
from backend.card_assets import PNG_ASSET_NAMES
from backend.image_processing import MAX_INPUT_BYTES, SIPS
from __tests__.fixtures import A, B, png_image


class WalletPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "Passes"
        self.root.mkdir()
        self.png = png_image(12, 8, lambda x, y: (20, 40, 80))
        wallet_preview._valid_image.cache_clear()
        self.addCleanup(wallet_preview._valid_image.cache_clear)

    def write(self, name, data=None, identifier=A, suffix=".pkpass"):
        path = self.root / "Cards" / (identifier + suffix) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png if data is None else data)
        return path

    def test_exact_id_and_full_card_allowlist_exclude_icons_logos_strips_and_placeholders(self):
        for name in ("icon.png", "logo.png", "strip.png", "background.png"):
            self.write(name)
        self.write("PlaceHolder", suffix=".cache")
        self.write(PNG_ASSET_NAMES[0], identifier=B)
        with patch.object(wallet_preview, "_decode_supported_image", return_value=True) as decoder:
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
            decoder.assert_not_called()
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, "../" + A))
        source = self.write(PNG_ASSET_NAMES[0])
        with patch.object(wallet_preview, "_decode_supported_image", return_value=True):
            self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), source)

    def test_full_face_priority_falls_back_from_opaque_cache_without_changing_it(self):
        opaque = self.write("FrontFace", b"proprietary rendered cache", suffix=".cache")
        background = self.write(PNG_ASSET_NAMES[1])
        with patch.object(wallet_preview, "_decode_supported_image", return_value=True) as decoder:
            self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), background)
            self.assertEqual(decoder.call_count, 1)
            preview = self.write("Preview", suffix=".cache")
            self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), preview)
        self.assertEqual(opaque.read_bytes(), b"proprietary rendered cache")

    def test_symlink_at_root_cards_card_directory_or_image_is_rejected(self):
        source = self.write(PNG_ASSET_NAMES[0])
        outside = Path(self.temporary.name) / "outside.png"
        outside.write_bytes(self.png)
        source.unlink()
        source.symlink_to(outside)
        with patch.object(wallet_preview, "_decode_supported_image", return_value=True) as decoder:
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
            source.unlink()
            source.parent.rmdir()
            source.parent.symlink_to(outside.parent, target_is_directory=True)
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
            source.parent.unlink()
            (self.root / "Cards").rmdir()
            (self.root / "Cards").symlink_to(outside.parent, target_is_directory=True)
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
            alias = Path(self.temporary.name) / "PassesAlias"
            alias.symlink_to(self.root, target_is_directory=True)
            self.assertIsNone(wallet_preview.find_cached_artwork(alias, A))
            decoder.assert_not_called()

    def test_size_dimensions_and_failed_decode_are_not_advertised(self):
        source = self.write(PNG_ASSET_NAMES[0])
        with source.open("wb") as stream:
            stream.truncate(MAX_INPUT_BYTES + 1)
        with patch.object(wallet_preview, "_decode_supported_image", return_value=True) as decoder:
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
            oversized = bytearray(self.png)
            struct.pack_into(">II", oversized, 16, 100000, 100000)
            source.write_bytes(oversized)
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
            decoder.assert_not_called()
        source.write_bytes(self.png)
        with patch.object(wallet_preview, "_decode_supported_image", return_value=False):
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))

    def test_decode_cache_tracks_replacement_and_deletion(self):
        source = self.write(PNG_ASSET_NAMES[0])
        with patch.object(wallet_preview, "_decode_supported_image", return_value=True) as decoder:
            self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), source)
            self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), source)
            self.assertEqual(decoder.call_count, 1)
            source.write_bytes(png_image(16, 10, lambda x, y: (100, 20, 40)))
            self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), source)
            self.assertEqual(decoder.call_count, 2)
            source.unlink()
            self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))

    @unittest.skipUnless(Path(SIPS).is_file(), "Requires the native macOS image decoder")
    def test_native_decoder_accepts_full_png_and_jpeg_and_rejects_corrupt_png(self):
        source = self.write("FrontFace", suffix=".cache")
        self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), source)
        jpeg = Path(self.temporary.name) / "fixture.jpg"
        subprocess.run([SIPS, "-s", "format", "jpeg", str(source), "--out", str(jpeg)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        source.write_bytes(jpeg.read_bytes())
        self.assertEqual(wallet_preview.find_cached_artwork(self.root, A), source)
        source.write_bytes(self.png[:33])
        self.assertIsNone(wallet_preview.find_cached_artwork(self.root, A))
