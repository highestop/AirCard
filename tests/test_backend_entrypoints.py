"""Exercise the packaged entrypoints without opening a server or writing a device."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackendEntrypointTests(unittest.TestCase):
    def test_browser_entrypoint_displays_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "backend", "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--no-browser", result.stdout)
        self.assertIn("--data-dir", result.stdout)

    def test_flash_module_rejects_invalid_id_before_native_write(self):
        result = subprocess.run(
            [sys.executable, "-u", "-m", "backend.aircard_backend", "--flash",
             "../invalid-device", "A" * 27 + "=", "unused.png"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "type": "error", "message": "Invalid device identifier.",
        })
